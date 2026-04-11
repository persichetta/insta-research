#!/usr/bin/env python3
"""
Refresh Instagram session from Firefox cookies.
Uses browser_cookie3 to read cookies directly from Firefox's profile.
"""
import sys
import instaloader
import browser_cookie3

INSTAGRAM_USERNAME = "pelobemdapesquisa"
SESSION_FILE = "session-file"

L = instaloader.Instaloader()

print("Reading Firefox cookies...")
try:
    cookiejar = browser_cookie3.firefox(domain_name=".instagram.com")
except Exception as e:
    print(f"ERROR: could not read Firefox cookies: {e}")
    print("Make sure Firefox is installed and you are logged into Instagram.")
    sys.exit(1)

for cookie in cookiejar:
    L.context._session.cookies.set(
        cookie.name, cookie.value,
        domain=cookie.domain, path=cookie.path
    )

username = L.test_login()
if not username:
    print("ERROR: authentication failed. Log into Instagram in Firefox and try again.")
    sys.exit(1)

# Required: set _username so instaloader recognizes the session as valid
L.context._username = username

L.save_session_to_file(SESSION_FILE)
print(f"Session saved for '{username}' -> {SESSION_FILE}")
