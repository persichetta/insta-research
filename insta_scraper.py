#!/usr/bin/env python3
"""
Instagram Political Narrative Scraper
GitHub Actions version: scrapes posts, filters by political keywords,
and appends new rows to a BigQuery table using load jobs (free tier compatible).
"""

import os
import sys
import re
import json
import time
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from collections import Counter

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

BQ_PROJECT = os.environ.get("BQ_PROJECT", "master-research-492307")
BQ_DATASET = os.environ.get("BQ_DATASET", "instagram_research")
BQ_TABLE = os.environ.get("BQ_TABLE", "political_posts")
BQ_FULL_TABLE = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"

START_DATE = datetime(2026, 1, 1)

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
    if not caption:
        return []
    matches = KEYWORD_PATTERN.findall(caption)
    return list(set(m.lower() for m in matches))


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
# BIGQUERY (load job, free tier compatible)
# ============================================================

def get_existing_shortcodes(client: bigquery.Client) -> set:
    query = f"SELECT shortcode FROM `{BQ_FULL_TABLE}`"
    try:
        results = client.query(query).result()
        codes = {row.shortcode for row in results}
        log.info(f"found {len(codes)} existing posts in BigQuery")
        return codes
    except Exception as e:
        log.warning(f"could not read existing shortcodes: {e}")
        return set()


def load_rows_to_bigquery(client: bigquery.Client, rows: list):
    """Load rows using a load job (works on free tier, unlike streaming insert)."""
    if not rows:
        log.info("no new rows to insert")
        return

    # write as newline-delimited JSON to temp file
    tmp_path = os.path.join(tempfile.gettempdir(), "bq_upload.jsonl")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )

    with open(tmp_path, "rb") as f:
        job = client.load_table_from_file(f, BQ_FULL_TABLE, job_config=job_config)

    job.result()  # wait for completion
    log.info(f"loaded {job.output_rows} rows into BigQuery")


# ============================================================
# SCRAPING
# ============================================================

def scrape_profile(L: instaloader.Instaloader, profile_name: str,
                   existing_shortcodes: set) -> list:
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

    L = setup_instaloader()
    client = bigquery.Client(project=BQ_PROJECT)

    existing = get_existing_shortcodes(client)

    all_new = []
    for profile_name in PROFILES_TO_SCRAPE:
        try:
            posts = scrape_profile(L, profile_name, existing)
            all_new.extend(posts)

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

    # load into BigQuery using load job (free tier compatible)
    load_rows_to_bigquery(client, all_new)

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
