#!/usr/bin/env python3
"""
Instagram Political Narrative Scraper
GitHub Actions version: scrapes posts, filters by political keywords,
and appends new rows to a BigQuery table.
"""

import os
import sys
import csv
import re
import json
import time
import logging
from datetime import datetime, timedelta
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

PROFILES_TO_SCRAPE = [
    "alfinetei",
    "babadeira",
]

# bigquery settings
BQ_PROJECT = os.environ.get("BQ_PROJECT", "master-research-492307")
BQ_DATASET = os.environ.get("BQ_DATASET", "instagram_research")
BQ_TABLE = os.environ.get("BQ_TABLE", "political_posts")
BQ_FULL_TABLE = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"

# absolute floor date (never scrape before this)
START_DATE = datetime(2026, 1, 1)

# political keywords
KEYWORDS = [
    "lula",
    "bolsonaro",
    "governo",
    "eleições",
    "eleicoes",
    "ministro",
    "candidato",
    "stf",
    "câmara",
    "camara",
    "senado",
    "deputados",
    "deputado",
]

KEYWORD_PATTERN = re.compile(
    "|".join(re.escape(kw) for kw in KEYWORDS),
    re.IGNORECASE,
)


# ============================================================
# HELPERS
# ============================================================

def caption_matches(caption: str) -> list:
    """Return list of matched political keywords in caption."""
    if not caption:
        return []
    matches = KEYWORD_PATTERN.findall(caption)
    return list(set(m.lower() for m in matches))


def setup_instaloader() -> instaloader.Instaloader:
    """Create instaloader instance and load session from file."""
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

def get_existing_shortcodes(client: bigquery.Client) -> set:
    """Fetch all shortcodes already in BigQuery to avoid duplicates."""
    query = f"SELECT shortcode FROM `{BQ_FULL_TABLE}`"
    try:
        results = client.query(query).result()
        codes = {row.shortcode for row in results}
        log.info(f"found {len(codes)} existing posts in BigQuery")
        return codes
    except Exception as e:
        # table might not exist yet on first run
        log.warning(f"could not read existing shortcodes: {e}")
        return set()


def ensure_table_exists(client: bigquery.Client):
    """Create the BigQuery table if it doesn't exist."""
    schema = [
        bigquery.SchemaField("profile", "STRING"),
        bigquery.SchemaField("shortcode", "STRING"),
        bigquery.SchemaField("post_url", "STRING"),
        bigquery.SchemaField("post_date", "TIMESTAMP"),
        bigquery.SchemaField("typename", "STRING"),
        bigquery.SchemaField("likes", "INTEGER"),
        bigquery.SchemaField("comments", "INTEGER"),
        bigquery.SchemaField("video_views", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("is_sponsored", "BOOLEAN"),
        bigquery.SchemaField("keywords_matched", "STRING"),
        bigquery.SchemaField("caption", "STRING"),
        bigquery.SchemaField("scraped_at", "TIMESTAMP"),
    ]

    table_ref = bigquery.Table(BQ_FULL_TABLE, schema=schema)
    table_ref.time_partitioning = bigquery.TimePartitioning(
        field="post_date",
        type_=bigquery.TimePartitioningType.MONTH,
    )

    try:
        client.get_table(BQ_FULL_TABLE)
        log.info(f"table {BQ_FULL_TABLE} already exists")
    except Exception:
        table = client.create_table(table_ref)
        log.info(f"created table {table.full_table_id}")


def insert_rows(client: bigquery.Client, rows: list):
    """Insert new rows into BigQuery."""
    if not rows:
        log.info("no new rows to insert")
        return

    errors = client.insert_rows_json(BQ_FULL_TABLE, rows)
    if errors:
        log.error(f"BigQuery insert errors: {errors}")
    else:
        log.info(f"inserted {len(rows)} new rows into BigQuery")


# ============================================================
# SCRAPING
# ============================================================

def scrape_profile(L: instaloader.Instaloader, profile_name: str,
                   existing_shortcodes: set) -> list:
    """Scrape one profile, return list of new post dicts."""
    log.info(f"scraping profile: {profile_name}")

    profile = instaloader.Profile.from_username(L.context, profile_name)
    log.info(f"  posts: {profile.mediacount} | followers: {profile.followers}")

    new_posts = []
    checked = 0
    old_count = 0
    now = datetime.utcnow().isoformat()

    for post in profile.get_posts():
        if post.date_utc < START_DATE:
            old_count += 1
            if old_count > 3:
                log.info(f"  reached posts before {START_DATE.date()}, stopping")
                break
            continue

        checked += 1

        # skip if already in BigQuery
        if post.shortcode in existing_shortcodes:
            log.info(f"  reached already-scraped post {post.shortcode}, stopping")
            break

        caption = post.caption or ""
        keywords_found = caption_matches(caption)

        if not keywords_found:
            continue

        row = {
            "profile": profile_name,
            "shortcode": post.shortcode,
            "post_url": f"https://www.instagram.com/p/{post.shortcode}/",
            "post_date": post.date_utc.isoformat(),
            "typename": post.typename,
            "likes": post.likes,
            "comments": post.comments,
            "video_views": post.video_view_count if post.is_video else None,
            "is_sponsored": post.is_sponsored,
            "keywords_matched": ", ".join(keywords_found),
            "caption": caption[:2000],
            "scraped_at": now,
        }

        new_posts.append(row)
        log.info(f"  NEW: {post.date_utc.strftime('%d/%m')} | "
                 f"{post.likes} likes | kw: {', '.join(keywords_found)}")

    log.info(f"  {profile_name}: checked {checked}, new political posts: {len(new_posts)}")
    return new_posts


# ============================================================
# MAIN
# ============================================================

def main():
    log.info("=" * 50)
    log.info("instagram political narrative scraper (daily)")
    log.info(f"profiles: {', '.join(PROFILES_TO_SCRAPE)}")
    log.info("=" * 50)

    # setup
    L = setup_instaloader()
    client = bigquery.Client(project=BQ_PROJECT)

    ensure_table_exists(client)
    existing = get_existing_shortcodes(client)

    # scrape each profile
    all_new = []
    for profile_name in PROFILES_TO_SCRAPE:
        try:
            posts = scrape_profile(L, profile_name, existing)
            all_new.extend(posts)

            # be nice to instagram between profiles
            if profile_name != PROFILES_TO_SCRAPE[-1]:
                log.info("waiting 30s before next profile...")
                time.sleep(30)

        except instaloader.exceptions.ProfileNotExistsException:
            log.error(f"profile '{profile_name}' not found (session expired?)")
            continue
        except instaloader.exceptions.ConnectionException as e:
            log.error(f"connection error for {profile_name}: {e}")
            time.sleep(60)
            continue
        except Exception as e:
            log.error(f"unexpected error for {profile_name}: {e}")
            continue

    # insert into BigQuery
    insert_rows(client, all_new)

    # summary
    log.info("=" * 50)
    log.info(f"total new political posts: {len(all_new)}")
    for profile_name in PROFILES_TO_SCRAPE:
        profile_posts = [p for p in all_new if p["profile"] == profile_name]
        if profile_posts:
            total_likes = sum(p["likes"] for p in profile_posts)
            kws = []
            for p in profile_posts:
                kws.extend(p["keywords_matched"].split(", "))
            top_kw = Counter(kws).most_common(5)
            log.info(f"  {profile_name}: {len(profile_posts)} posts, "
                     f"{total_likes:,} likes, "
                     f"top kw: {', '.join(f'{k}({n})' for k, n in top_kw)}")
    log.info("done!")


if __name__ == "__main__":
    main()
