"""MCP control plane for the 24/7 Chrome server.

The MCP server controls the Selenium session owned by keepalive.py.
It deliberately exposes browser-control operations, not raw shell execution
or credential extraction.
"""
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from mcp.server.mcpserver import MCPServer

BASE = os.getenv("SELENIUM_URL", "http://127.0.0.1:4444/wd/hub").rstrip("/")
DEFAULT_TIMEOUT = float(os.getenv("MCP_BROWSER_TIMEOUT_SECONDS", "30"))

mcp = MCPServer(
    "24/7 Chrome Browser Control",
    instructions=(
        "Control the server-side Chromium session through safe browser tools. "
        "Do not request or expose passwords, cookies, access tokens, or other secrets."
    ),
)


def request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as response:
        raw = response.read().decode()
        return json.loads(raw) if raw else {}


def value(result: Any) -> Any:
    return result.get("value", result) if isinstance(result, dict) else result


def session_id() -> str | None:
    # keepalive.py owns the session. Discover the active session without
    # creating a second browser session.
    try:
        sessions = request("GET", "/sessions")
        items = value(sessions) or []
        if isinstance(items, list) and items:
            return items[0].get("id")
    except Exception:
        return None
    return None


def require_session() -> str:
    sid = session_id()
    if not sid:
        raise RuntimeError("No active browser session. Wait for keepalive or restart the browser.")
    return sid


@mcp.tool()
def browser_status() -> dict[str, Any]:
    """Return browser readiness, active session id presence, URL, title and tab count."""
    try:
        sid = session_id()
        if not sid:
            return {"ready": False, "reason": "no_active_session"}
        handles = value(request("GET", f"/session/{sid}/window/handles")) or []
        current = value(request("GET", f"/session/{sid}/url"))
        title = value(request("GET", f"/session/{sid}/title"))
        return {
            "ready": True,
            "session_active": True,
            "tab_count": len(handles) if isinstance(handles, list) else 0,
            "current_url": current,
            "title": title,
        }
    except Exception as exc:
        return {"ready": False, "error": str(exc)}


@mcp.tool()
def list_tabs() -> list[dict[str, Any]]:
    """List open browser tabs with safe metadata (handle, URL and title)."""
    sid = require_session()
    handles = value(request("GET", f"/session/{sid}/window/handles")) or []
    current = value(request("GET", f"/session/{sid}/window"))
    result = []
    for handle in handles:
        request("POST", f"/session/{sid}/window", {"handle": handle})
        result.append({
            "handle": handle,
            "url": value(request("GET", f"/session/{sid}/url")),
            "title": value(request("GET", f"/session/{sid}/title")),
            "current": handle == current,
        })
    if current in handles:
        request("POST", f"/session/{sid}/window", {"handle": current})
    return result


@mcp.tool()
def navigate(url: str) -> dict[str, Any]:
    """Navigate the current tab to a URL. Only http/https URLs are accepted."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("Only http:// and https:// URLs are allowed.")
    sid = require_session()
    request("POST", f"/session/{sid}/url", {"url": url})
    return {
        "url": value(request("GET", f"/session/{sid}/url")),
        "title": value(request("GET", f"/session/{sid}/title")),
    }


@mcp.tool()
def open_tab(url: str) -> dict[str, Any]:
    """Open a new browser tab at an http/https URL."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("Only http:// and https:// URLs are allowed.")
    sid = require_session()
    new_handle = value(request("POST", f"/session/{sid}/window/new", {"type": "tab"})).get("handle")
    request("POST", f"/session/{sid}/window", {"handle": new_handle})
    request("POST", f"/session/{sid}/url", {"url": url})
    return {"handle": new_handle, "url": url, "title": value(request("GET", f"/session/{sid}/title"))}


@mcp.tool()
def switch_tab(handle: str) -> dict[str, Any]:
    """Switch to a tab handle returned by list_tabs."""
    sid = require_session()
    handles = value(request("GET", f"/session/{sid}/window/handles")) or []
    if handle not in handles:
        raise ValueError("Unknown tab handle.")
    request("POST", f"/session/{sid}/window", {"handle": handle})
    return {"handle": handle, "url": value(request("GET", f"/session/{sid}/url")), "title": value(request("GET", f"/session/{sid}/title"))}


@mcp.tool()
def close_tab(handle: str) -> dict[str, Any]:
    """Close a tab by handle, while keeping the browser session alive."""
    sid = require_session()
    handles = value(request("GET", f"/session/{sid}/window/handles")) or []
    if handle not in handles:
        raise ValueError("Unknown tab handle.")
    if len(handles) <= 1:
        raise ValueError("Refusing to close the last browser tab.")
    current = value(request("GET", f"/session/{sid}/window"))
    request("POST", f"/session/{sid}/window", {"handle": handle})
    request("DELETE", f"/session/{sid}/window")
    if current in handles and current != handle:
        request("POST", f"/session/{sid}/window", {"handle": current})
    return {"closed": handle, "remaining_tabs": len(handles) - 1}


@mcp.tool()
def refresh_page() -> dict[str, Any]:
    """Refresh the current page."""
    sid = require_session()
    request("POST", f"/session/{sid}/refresh")
    return {"url": value(request("GET", f"/session/{sid}/url")), "title": value(request("GET", f"/session/{sid}/title"))}


@mcp.tool()
def page_text(max_chars: int = 12000) -> str:
    """Return visible page text from the current tab, truncated to max_chars."""
    sid = require_session()
    result = value(request("GET", f"/session/{sid}/element/active/name")) if False else None
    # WebDriver does not provide a standard visible-text endpoint; use the
    # document body text through a constrained script execution.
    script = "return document && document.body ? document.body.innerText : '';"
    result = value(request("POST", f"/session/{sid}/execute/sync", {"script": script, "args": []}))
    text = str(result or "")
    return text[:max(100, min(max_chars, 50000))]


@mcp.tool()
def browser_health() -> dict[str, Any]:
    """Check Selenium and the active browser session without changing page state."""
    try:
        selenium = value(request("GET", "/status"))
        sid = session_id()
        return {
            "selenium_ready": bool(selenium.get("ready", False)) if isinstance(selenium, dict) else True,
            "browser_session": bool(sid),
            "timestamp": int(time.time()),
        }
    except Exception as exc:
        return {"selenium_ready": False, "browser_session": False, "error": str(exc)}


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.getenv("MCP_PORT", "8090")),
        json_response=True,
        transport_security=None,
    )
