#!/usr/bin/env python3
"""
Backup option: refresh Instagram session from Firefox and push to GitHub Secrets.

Use when the self-hosted runner is not available (machine offline) and you
need the GitHub-hosted runner to work for one run. Run this locally the night
before the scheduled workflow (Saturday before the Sunday 06:00 UTC job).

Requirements:
  - pip install instaloader
  - gh CLI installed and authenticated: https://cli.github.com/
    (run 'gh auth login' once)
  - Firefox with active Instagram session

NOTE: Even with a fresh session, GitHub-hosted runners use Azure datacenter
IPs that Instagram may reject. This is a fallback — the self-hosted runner
approach is more reliable.
"""

import sys
import base64
import subprocess
import tempfile
import os

import instaloader

INSTAGRAM_USERNAME = "pelobemdapesquisa"
GITHUB_REPO = "persichetta/insta-research"
SECRET_NAME = "INSTA_SESSION_BASE64"


def refresh_session() -> bytes:
    L = instaloader.Instaloader()
    print("Loading Firefox cookies...")

    try:
        L.load_cookies_from_browser("firefox")
    except Exception as e:
        print(f"ERROR: could not load Firefox cookies: {e}")
        print("Make sure you are logged into Instagram in Firefox.")
        sys.exit(1)

    username = L.test_login()
    if not username:
        print("ERROR: authentication failed.")
        print("Log into Instagram in Firefox and try again.")
        sys.exit(1)

    print(f"Authenticated as: {username}")

    fd, tmpfile = tempfile.mkstemp(suffix="-insta-session")
    os.close(fd)
    try:
        L.save_session_to_file(tmpfile)
        with open(tmpfile, "rb") as f:
            return f.read()
    finally:
        os.unlink(tmpfile)


def update_github_secret(session_bytes: bytes):
    session_b64 = base64.b64encode(session_bytes).decode("ascii")

    result = subprocess.run(
        ["gh", "secret", "set", SECRET_NAME,
         "--repo", GITHUB_REPO,
         "--body", session_b64],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"ERROR updating GitHub Secret: {result.stderr.strip()}")
        print("Make sure 'gh' is installed and you ran 'gh auth login'.")
        sys.exit(1)

    print(f"GitHub Secret '{SECRET_NAME}' updated in {GITHUB_REPO}")


if __name__ == "__main__":
    session_bytes = refresh_session()
    update_github_secret(session_bytes)
    print("Done. The next GitHub Actions run will use the fresh session.")
