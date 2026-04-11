#!/usr/bin/env python3
"""
Refresh Instagram session from Firefox cookies.

Called by GitHub Actions (self-hosted runner) before the scraper runs.
Saves 'session-file' in the current working directory.

Requires:
  - Firefox installed on the machine running this script
  - Logged into Instagram in Firefox (session must still be active)
"""

import sys
import instaloader

INSTAGRAM_USERNAME = "pelobemdapesquisa"
SESSION_FILE = "session-file"


def main():
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
        print("ERROR: authentication failed after loading Firefox cookies.")
        print("Log into Instagram in Firefox and try again.")
        sys.exit(1)

    L.save_session_to_file(SESSION_FILE)
    print(f"Session saved for '{username}' -> {SESSION_FILE}")


if __name__ == "__main__":
    main()
