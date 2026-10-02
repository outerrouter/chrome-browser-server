"""MCP control plane for the 24/7 Chrome server.

The MCP server controls the Selenium session owned by keepalive.py.
It exposes browser operations only; no shell execution or credential extraction.
"""
import json, os, time, urllib.request
from typing import Any
from mcp.server.mcpserver import MCPServer
BASE=os.getenv("SELENIUM_URL","http://127.0.0.1:4444/wd/hub").rstrip("/")
TIMEOUT=float(os.getenv("MCP_BROWSER_TIMEOUT_SECONDS","30"))
mcp=MCPServer("24/7 Chrome Browser Control", instructions="Control the server-side Chromium session through browser tools. Never request or expose passwords, cookies, access tokens, or secrets.")
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
if __name__=="__main__": mcp.run(transport="streamable-http",host="0.0.0.0",port=int(os.getenv("MCP_PORT","8090")),json_response=True,stateless_http=True)