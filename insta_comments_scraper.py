#!/usr/bin/env python3
"""
Instagram Political Comments Scraper
Collects ALL comments from political posts already in BigQuery.
Designed to run weekly via GitHub Actions, processing only posts
whose comments haven't been scraped yet.
"""

import os
import sys
import re
import json
import time
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import instaloader
from google.cloud import bigquery

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

INSTAGRAM_USERNAME = os.environ.get("INSTAGRAM_USERNAME", "pelobemdapesquisa")

BQ_PROJECT = os.environ.get("BQ_PROJECT", "master-research-492307")
BQ_DATASET = os.environ.get("BQ_DATASET", "instagram_research")
BQ_POSTS_TABLE = f"{BQ_PROJECT}.{BQ_DATASET}.political_posts"
BQ_COMMENTS_TABLE = f"{BQ_PROJECT}.{BQ_DATASET}.political_comments"

# max posts to process per run (to avoid rate limiting)
MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "15"))

# delay between posts (seconds)
DELAY_BETWEEN_POSTS = int(os.environ.get("DELAY_BETWEEN_POSTS", "10"))

# max comments per post (0 = no limit)
MAX_COMMENTS_PER_POST = int(os.environ.get("MAX_COMMENTS_PER_POST", "0"))


# ============================================================
# INSTALOADER SETUP
# ============================================================

def setup_instaloader() -> instaloader.Instaloader:
    L = instaloader.Instaloader(
        download_video_thumbnails=False,
        download_pictures=False,
        download_videos=False,
        download_geotags=False,
        save_metadata=False,
        post_metadata_txt_pattern="",
        compress_json=False,
    )

    session_file = os.environ.get("INSTA_SESSION_FILE", "session-file")
    if not Path(session_file).exists():
        log.error(f"session file not found: {session_file}")
        sys.exit(1)

    L.load_session_from_file(INSTAGRAM_USERNAME, session_file)
    username = L.test_login()
    if not username:
        log.error("session is expired or invalid")
        sys.exit(1)

    log.info(f"logged in as: {username}")
    return L


# ============================================================
# BIGQUERY
# ============================================================

def ensure_comments_table(client: bigquery.Client):
    """Create political_comments table if it doesn't exist."""
    schema = [
        bigquery.SchemaField("comment_id", "STRING"),
        bigquery.SchemaField("shortcode", "STRING"),
        bigquery.SchemaField("profile", "STRING"),
        bigquery.SchemaField("comment_username", "STRING"),
        bigquery.SchemaField("comment_text", "STRING"),
        bigquery.SchemaField("comment_date", "TIMESTAMP"),
        bigquery.SchemaField("comment_likes", "INTEGER"),
        bigquery.SchemaField("is_reply", "BOOLEAN"),
        bigquery.SchemaField("scraped_at", "TIMESTAMP"),
    ]

    table_ref = bigquery.Table(BQ_COMMENTS_TABLE, schema=schema)

    try:
        client.get_table(BQ_COMMENTS_TABLE)
        log.info(f"table {BQ_COMMENTS_TABLE} already exists")
    except Exception:
        table = client.create_table(table_ref)
        log.info(f"created table {table.full_table_id}")


def get_posts_needing_comments(client: bigquery.Client) -> list:
    """Get political posts that haven't had their comments scraped yet."""
    query = f"""
    SELECT p.shortcode, p.profile, p.comments AS comment_count
    FROM `{BQ_POSTS_TABLE}` p
    LEFT JOIN (
        SELECT shortcode, COUNT(*) AS scraped_count
        FROM `{BQ_COMMENTS_TABLE}`
        GROUP BY shortcode
    ) c ON p.shortcode = c.shortcode
    WHERE c.shortcode IS NULL
    ORDER BY p.post_date DESC
    LIMIT {MAX_POSTS_PER_RUN}
    """
    try:
        results = client.query(query).result()
        posts = [{"shortcode": r.shortcode, "profile": r.profile,
                  "comment_count": r.comment_count} for r in results]
        log.info(f"found {len(posts)} posts needing comment scraping")
        return posts
    except Exception as e:
        log.error(f"error querying posts: {e}")
        return []


def load_comments_to_bigquery(client: bigquery.Client, rows: list):
    """Load comment rows using a load job (free tier compatible)."""
    if not rows:
        return

    tmp_path = os.path.join(tempfile.gettempdir(), "bq_comments.jsonl")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )

    with open(tmp_path, "rb") as f:
        job = client.load_table_from_file(f, BQ_COMMENTS_TABLE, job_config=job_config)

    job.result()
    log.info(f"loaded {job.output_rows} comments into BigQuery")


# ============================================================
# SCRAPING
# ============================================================

def scrape_comments_for_post(L: instaloader.Instaloader,
                              shortcode: str, profile: str) -> list:
    """Scrape all comments for a single post."""
    now = datetime.now(timezone.utc).isoformat()
    comments = []

    try:
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        comment_count = 0

        for comment in post.get_comments():
            comment_row = {
                "comment_id": str(comment.id),
                "shortcode": shortcode,
                "profile": profile,
                "comment_username": comment.owner.username if comment.owner else "unknown",
                "comment_text": comment.text or "",
                "comment_date": comment.created_at_utc.isoformat() if comment.created_at_utc else now,
                "comment_likes": comment.likes_count if hasattr(comment, 'likes_count') else 0,
                "is_reply": False,
                "scraped_at": now,
            }
            comments.append(comment_row)
            comment_count += 1

            # also get replies to this comment
            if hasattr(comment, 'answers'):
                for reply in comment.answers:
                    reply_row = {
                        "comment_id": str(reply.id),
                        "shortcode": shortcode,
                        "profile": profile,
                        "comment_username": reply.owner.username if reply.owner else "unknown",
                        "comment_text": reply.text or "",
                        "comment_date": reply.created_at_utc.isoformat() if reply.created_at_utc else now,
                        "comment_likes": reply.likes_count if hasattr(reply, 'likes_count') else 0,
                        "is_reply": True,
                        "scraped_at": now,
                    }
                    comments.append(reply_row)
                    comment_count += 1

            if MAX_COMMENTS_PER_POST > 0 and comment_count >= MAX_COMMENTS_PER_POST:
                log.info(f"    reached max comments limit ({MAX_COMMENTS_PER_POST})")
                break

    except instaloader.exceptions.QueryReturnedNotFoundException:
        log.warning(f"  post {shortcode} not found (deleted?)")
    except Exception as e:
        log.error(f"  error scraping comments for {shortcode}: {e}")

    return comments


# ============================================================
# MAIN
# ============================================================

def main():
    log.info("=" * 50)
    log.info("instagram political comments scraper")
    log.info(f"max posts per run: {MAX_POSTS_PER_RUN}")
    log.info("=" * 50)

    L = setup_instaloader()
    client = bigquery.Client(project=BQ_PROJECT)

    ensure_comments_table(client)
    posts = get_posts_needing_comments(client)

    if not posts:
        log.info("all posts already have comments scraped. nothing to do.")
        return

    total_comments = 0

    for i, post_info in enumerate(posts):
        shortcode = post_info["shortcode"]
        profile = post_info["profile"]
        expected = post_info["comment_count"]

        log.info(f"[{i+1}/{len(posts)}] scraping comments for {shortcode} "
                 f"({profile}, ~{expected} comments)")

        comments = scrape_comments_for_post(L, shortcode, profile)

        if comments:
            load_comments_to_bigquery(client, comments)
            total_comments += len(comments)
            log.info(f"  scraped {len(comments)} comments")

        # delay between posts to avoid rate limiting
        if i < len(posts) - 1:
            log.info(f"  waiting {DELAY_BETWEEN_POSTS}s...")
            time.sleep(DELAY_BETWEEN_POSTS)

    log.info("=" * 50)
    log.info(f"done! total comments scraped: {total_comments}")
    log.info(f"posts processed: {len(posts)}")


if __name__ == "__main__":
    main()
