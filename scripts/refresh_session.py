#!/usr/bin/env python3
"""
Refresh Instagram session from Firefox cookies.
Saves a pickle file compatible with instaloader's load_session_from_file.
"""
import sys
import pickle
import instaloader
import browser_cookie3

INSTAGRAM_USERNAME = "pelobemdapesquisa"
SESSION_FILE = "session-file"

# 1. Read Firefox cookies
print("Reading Firefox cookies...", flush=True)
try:
    cookiejar = browser_cookie3.firefox(domain_name=".instagram.com")
    cookies = list(cookiejar)
except Exception as e:
    print(f"ERROR reading Firefox cookies: {e}", flush=True)
    print("Make sure Firefox is installed and logged into Instagram.", flush=True)
    sys.exit(1)

# 2. Verify sessionid cookie is present
sessionid = next((c.value for c in cookies if c.name == "sessionid"), None)
if not sessionid:
    print("ERROR: 'sessionid' cookie not found in Firefox.", flush=True)
    print("Log into Instagram in Firefox and try again.", flush=True)
    sys.exit(1)

print(f"Found {len(cookies)} Instagram cookies (sessionid present).", flush=True)

# 3. Build instaloader session and inject cookies
L = instaloader.Instaloader()
for c in cookies:
    L.context._session.cookies.set(c.name, c.value, domain=c.domain, path=c.path)

# 4. Save directly as pickle — same format load_session_from_file expects,
#    bypasses the @requires_login decorator on save_session_to_file
with open(SESSION_FILE, "wb") as f:
    pickle.dump(L.context._session.cookies, f)

print(f"Session saved -> {SESSION_FILE}", flush=True)
