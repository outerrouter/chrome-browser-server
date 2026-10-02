#!/usr/bin/env python3
"""Keep one Selenium Chrome session alive and maintain configured tabs."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.getenv("SELENIUM_URL", "http://127.0.0.1:4444/wd/hub").rstrip("/")
URLS = [u.strip() for u in os.getenv("KEEPALIVE_URLS", "https://example.com").split(",") if u.strip()]
INTERVAL = max(10, int(os.getenv("KEEPALIVE_INTERVAL_SECONDS", "60")))
DURATION_MINUTES = max(0, int(os.getenv("BROWSER_DURATION_MINUTES", "0")))
DEADLINE = time.monotonic() + DURATION_MINUTES * 60 if DURATION_MINUTES else None


def request(method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.loads(response.read().decode() or "{}")


def create_session():
    payload = {
        "capabilities": {
            "alwaysMatch": {
                "browserName": "chrome",
                "goog:chromeOptions": {
                    "args": ["--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check", "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=9222", "--user-data-dir=/home/seluser/chrome-profile"]
                },
            }
        }
    }
    result = request("POST", "/session", payload)
    return result.get("value", {}).get("sessionId") or result.get("sessionId")


def open_url(session_id, url):
    request("POST", f"/session/{session_id}/url", {"url": url})


def new_tab(session_id, url):
    request("POST", f"/session/{session_id}/window/new", {"type": "tab"})
    open_url(session_id, url)


def delete_session(session_id):
    if not session_id:
        return
    try:
        request("DELETE", f"/session/{session_id}")
    except Exception:
        pass


def main():
    session_id = None
    if not URLS:
        raise RuntimeError("KEEPALIVE_URLS must contain at least one URL")
    if DEADLINE:
        print(f"[keepalive] duration set to {DURATION_MINUTES} minute(s)", flush=True)
    else:
        print("[keepalive] duration unlimited (BROWSER_DURATION_MINUTES=0)", flush=True)

    try:
        while True:
            if DEADLINE and time.monotonic() >= DEADLINE:
                print("[keepalive] configured duration complete; stopping cleanly", flush=True)
                break
            try:
                if not session_id:
                    session_id = create_session()
                    if not session_id:
                        raise RuntimeError("Selenium did not return a session id")
                    print(f"[keepalive] created session {session_id}", flush=True)
                    open_url(session_id, URLS[0])
                    for url in URLS[1:]:
                        new_tab(session_id, url)
                    print(f"[keepalive] maintaining {len(URLS)} tab(s)", flush=True)
                else:
                    request("GET", f"/session/{session_id}/window/handles")
                sleep_for = INTERVAL if not DEADLINE else min(INTERVAL, max(1, int(DEADLINE - time.monotonic())))
                time.sleep(sleep_for)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
                print(f"[keepalive] session unavailable: {exc}; retrying", file=sys.stderr, flush=True)
                delete_session(session_id)
                session_id = None
                time.sleep(5)
            except Exception as exc:
                print(f"[keepalive] unexpected error: {exc}; retrying", file=sys.stderr, flush=True)
                delete_session(session_id)
                session_id = None
                time.sleep(5)
    finally:
        delete_session(session_id)


if __name__ == "__main__":
    main()
