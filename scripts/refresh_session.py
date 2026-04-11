#!/usr/bin/env python3
import sys
import instaloader

INSTAGRAM_USERNAME = "pelobemdapesquisa"
SESSION_FILE = "session-file"

L = instaloader.Instaloader()
print("Loading Firefox cookies...")
try:
    L.load_cookies_from_browser("firefox")
except Exception as e:
    print(f"ERROR: could not load Firefox cookies: {e}")
    sys.exit(1)

username = L.test_login()
if not username:
    print("ERROR: authentication failed.")
    sys.exit(1)

L.save_session_to_file(SESSION_FILE)
print(f"Session saved for '{username}' -> {SESSION_FILE}")
