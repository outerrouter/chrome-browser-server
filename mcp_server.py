"""MCP control plane for the 24/7 Chrome server.

The MCP server controls the Selenium session owned by keepalive.py.
It exposes browser operations only; no shell execution or credential extraction.
"""
import json, os, time, urllib.request
from typing import Any
from agent_runtime import manager
from mcp.server.mcpserver import MCPServer, Context
try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None
BASE=os.getenv("SELENIUM_URL","http://127.0.0.1:4444/wd/hub").rstrip("/")
TIMEOUT=float(os.getenv("MCP_BROWSER_TIMEOUT_SECONDS","30"))
mcp=MCPServer("24/7 Chrome Browser Control", instructions="Control the server-side Chromium session through browser tools. Long-running agent tasks emit live progress/events; human approval is required for authentication and security checks. Never request or expose passwords, cookies, access tokens, or secrets.")
PUBLIC_BASE=os.getenv("PUBLIC_BASE_URL","").rstrip("/")
def request(method: str, path: str, payload: dict[str,Any]|None=None)->Any:
    body=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(BASE+path,data=body,method=method,headers={"Content-Type":"application/json","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=TIMEOUT) as response:
        raw=response.read().decode(); return json.loads(raw) if raw else {}
def value(result: Any)->Any: return result.get("value",result) if isinstance(result,dict) else result
def active_session()->str|None:
    try:
        result=value(request("GET","/sessions")); return result[0].get("id") if isinstance(result,list) and result else None
    except Exception: return None
def require_session()->str:
    sid=active_session()
    if not sid: raise RuntimeError("No active browser session. Wait for keepalive or restart the browser.")
    return sid
@mcp.tool()
def browser_status()->dict[str,Any]:
    """Return readiness, current URL/title and number of open tabs."""
    try:
        sid=active_session()
        if not sid: return {"ready":False,"reason":"no_active_session"}
        handles=value(request("GET",f"/session/{sid}/window/handles")) or []
        return {"ready":True,"session_active":True,"tab_count":len(handles),"current_url":value(request("GET",f"/session/{sid}/url")),"title":value(request("GET",f"/session/{sid}/title"))}
    except Exception as exc: return {"ready":False,"error":str(exc)}
@mcp.tool()
def list_tabs()->list[dict[str,Any]]:
    """List open tabs with handle, URL, title and current-tab state."""
    sid=require_session(); handles=value(request("GET",f"/session/{sid}/window/handles")) or []; current=value(request("GET",f"/session/{sid}/window")); out=[]
    for handle in handles:
        request("POST",f"/session/{sid}/window",{"handle":handle}); out.append({"handle":handle,"url":value(request("GET",f"/session/{sid}/url")),"title":value(request("GET",f"/session/{sid}/title")),"current":handle==current})
    if current in handles: request("POST",f"/session/{sid}/window",{"handle":current})
    return out
def validate_url(url:str)->None:
    if not url.startswith(("http://","https://")): raise ValueError("Only http:// and https:// URLs are allowed.")
@mcp.tool()
def navigate(url:str)->dict[str,Any]:
    """Navigate the current tab to an HTTP(S) URL."""
    validate_url(url); sid=require_session(); request("POST",f"/session/{sid}/url",{"url":url}); return {"url":value(request("GET",f"/session/{sid}/url")),"title":value(request("GET",f"/session/{sid}/title"))}
@mcp.tool()
def open_tab(url:str)->dict[str,Any]:
    """Open a new tab at an HTTP(S) URL."""
    validate_url(url); sid=require_session(); result=value(request("POST",f"/session/{sid}/window/new",{"type":"tab"})); handle=result.get("handle"); request("POST",f"/session/{sid}/window",{"handle":handle}); request("POST",f"/session/{sid}/url",{"url":url}); return {"handle":handle,"url":url,"title":value(request("GET",f"/session/{sid}/title"))}
@mcp.tool()
def switch_tab(handle:str)->dict[str,Any]:
    """Switch to a tab handle returned by list_tabs."""
    sid=require_session(); handles=value(request("GET",f"/session/{sid}/window/handles")) or []
    if handle not in handles: raise ValueError("Unknown tab handle.")
    request("POST",f"/session/{sid}/window",{"handle":handle}); return {"handle":handle,"url":value(request("GET",f"/session/{sid}/url")),"title":value(request("GET",f"/session/{sid}/title"))}
@mcp.tool()
def close_tab(handle:str)->dict[str,Any]:
    """Close a tab by handle while keeping the browser session alive."""
    sid=require_session(); handles=value(request("GET",f"/session/{sid}/window/handles")) or []
    if handle not in handles: raise ValueError("Unknown tab handle.")
    if len(handles)<=1: raise ValueError("Refusing to close the last browser tab.")
    current=value(request("GET",f"/session/{sid}/window")); request("POST",f"/session/{sid}/window",{"handle":handle}); request("DELETE",f"/session/{sid}/window")
    if current in handles and current!=handle: request("POST",f"/session/{sid}/window",{"handle":current})
    return {"closed":handle,"remaining_tabs":len(handles)-1}
@mcp.tool()
def refresh_page()->dict[str,Any]:
    """Refresh the current tab."""
    sid=require_session(); request("POST",f"/session/{sid}/refresh"); return {"url":value(request("GET",f"/session/{sid}/url")),"title":value(request("GET",f"/session/{sid}/title"))}
@mcp.tool()
def page_text(max_chars:int=12000)->str:
    """Return visible text from the current page, capped for safe context size."""
    sid=require_session(); max_chars=max(100,min(max_chars,50000)); script="return document && document.body ? document.body.innerText : \"\";"
    text=value(request("POST",f"/session/{sid}/execute/sync",{"script":script,"args":[]})); return str(text or "")[:max_chars]
@mcp.tool()
def browser_health()->dict[str,Any]:
    """Check Selenium and browser-session health."""
    try:
        selenium=value(request("GET","/status")); return {"selenium_ready":bool(selenium.get("ready",False)) if isinstance(selenium,dict) else True,"browser_session":bool(active_session()),"timestamp":int(time.time())}
    except Exception as exc: return {"selenium_ready":False,"browser_session":False,"error":str(exc)}
@mcp.tool()
def cdp_status() -> dict[str, Any]:
    """Check whether the running Chrome exposes a local CDP endpoint."""
    endpoint = os.getenv("CDP_ENDPOINT", "http://127.0.0.1:9222")
    try:
        with urllib.request.urlopen(endpoint + "/json/version", timeout=5) as response:
            data = json.loads(response.read().decode())
        return {"available": True, "browser": data.get("Browser"), "webSocketDebuggerUrl_present": bool(data.get("webSocketDebuggerUrl"))}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


@mcp.tool()
def accessibility_snapshot(max_chars: int = 30000) -> str:
    """Return an accessibility-oriented page snapshot through Playwright over CDP."""
    if async_playwright is None:
        raise RuntimeError("Playwright package is not installed.")
    import asyncio
    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(os.getenv("CDP_ENDPOINT", "http://127.0.0.1:9222"))
            contexts = browser.contexts
            if not contexts or not contexts[0].pages:
                return "No browser page is available."
            page = contexts[0].pages[0]
            # ARIA snapshot is structured text and avoids exposing browser storage.
            try:
                result = await page.locator("body").aria_snapshot(timeout=5000)
            except Exception:
                result = await page.locator("body").inner_text(timeout=5000)

            return result[:max(100, min(max_chars, 60000))]
    return asyncio.run(run())


@mcp.tool()
def click_text(text: str) -> dict[str, Any]:
    """Click a visible element by exact text using Playwright over CDP."""
    if async_playwright is None:
        raise RuntimeError("Playwright package is not installed.")
    import asyncio
    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(os.getenv("CDP_ENDPOINT", "http://127.0.0.1:9222"))
            page = browser.contexts[0].pages[0]
            locator = page.get_by_text(text, exact=True).first
            await locator.click(timeout=10000)
            result = {"url": page.url, "title": await page.title()}

            return result
    return asyncio.run(run())


@mcp.tool()
def fill_label(label: str, value: str) -> dict[str, Any]:
    """Fill a visible form field by label. Sensitive authentication values should not be sent here."""
    if async_playwright is None:
        raise RuntimeError("Playwright package is not installed.")
    import asyncio
    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(os.getenv("CDP_ENDPOINT", "http://127.0.0.1:9222"))
            page = browser.contexts[0].pages[0]
            await page.get_by_label(label, exact=True).fill(value)
            result = {"url": page.url, "title": await page.title()}

            return result
    return asyncio.run(run())

@mcp.tool()
def agent_run(goal: str, plan_json: str = "") -> dict[str, Any]:
    """Start a background Planner -> Executor -> Verifier browser task.
    
    plan_json is optional JSON array of safe actions: navigate, refresh,
    click_text, fill_label, wait, snapshot, verify, approval.
    Authentication secrets, OTPs and security challenges are not accepted.
    """
    steps = None
    if plan_json.strip():
        steps = json.loads(plan_json)
    return manager.start(goal, steps)

@mcp.tool()
def agent_status(task_id: str) -> dict[str, Any]:
    """Return the current state, plan, results and approval state of an agent task."""
    return manager.status(task_id)

@mcp.tool()
def agent_approve(task_id: str) -> dict[str, Any]:
    """Resume a task that is explicitly waiting for human approval."""
    return manager.approve(task_id)

@mcp.tool()
def agent_pause(task_id: str) -> dict[str, Any]:
    """Pause a running agent task before its next step."""
    return manager.pause(task_id)

@mcp.tool()
def agent_stop(task_id: str) -> dict[str, Any]:
    """Stop an agent task."""
    return manager.stop(task_id)


@mcp.tool()
def agent_events(task_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
    """Return live task events after a sequence number. Events include step, action, browser URL and human-approval state."""
    items = manager.events_since(task_id, after_seq)
    if PUBLIC_BASE:
        for item in items:
            item["takeover_link"] = PUBLIC_BASE + "/"
    return items


@mcp.tool()
async def agent_watch(task_id: str, ctx: Context, poll_seconds: float = 1.0) -> dict[str, Any]:
    """Keep an MCP call open and stream agent step-by-step progress until the task finishes."""
    import asyncio
    seq = 0
    while True:
        status = manager.status(task_id)
        events_now = await asyncio.to_thread(manager.wait_events, task_id, seq, max(1.0, min(15.0, poll_seconds)))
        for event in events_now:
            seq = max(seq, int(event["seq"]))
            step = int(event.get("step", status.get("step_index", 0)))
            total = max(1, int(event.get("total", len(status.get("plan", [])) or 1)))
            message = event.get("message", "Agent update")
            if PUBLIC_BASE:
                message += " | Browser takeover: " + PUBLIC_BASE + "/"
            try:
                await ctx.report_progress(min(step, total), total, message)
            except Exception:
                pass
            try:
                import mcp.types as types
                await ctx.request_context.session.send_notification(
                    types.LoggingMessageNotification(
                        params=types.LoggingMessageNotificationParams(level="info", logger="browser-agent", data=message)
                    )
                )
            except Exception:
                pass
        status = manager.status(task_id)
        if status["status"] in {"completed", "failed", "stopped"}:
            return {"task": status, "last_seq": seq}
        if status["status"] == "paused":
            return {"task": status, "last_seq": seq, "waiting_for_human": True}


if __name__=="__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.getenv("MCP_PORT","8090")),
        json_response=False,
        stateless_http=False,
        event_store=None,
        session_idle_timeout=None,
    )
