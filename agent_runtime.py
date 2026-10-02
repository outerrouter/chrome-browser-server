"""Small, dependency-light Planner -> Executor -> Verifier runtime.

The runtime deliberately does not expose shell/RCE or credentials. It executes a
structured browser plan in a background thread and pauses for human approval on
sensitive authentication fields or explicit approval steps.
"""
from __future__ import annotations
import json, re, threading, time, uuid
from typing import Any
import urllib.request

BASE="http://127.0.0.1:4444/wd/hub"
MAX_STEPS=20

def _request(method:str,path:str,payload:dict[str,Any]|None=None)->Any:
    body=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(BASE+path,data=body,method=method,
        headers={"Content-Type":"application/json","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read().decode()
    obj=json.loads(raw) if raw else {}
    return obj.get("value",obj) if isinstance(obj,dict) else obj

def _sid()->str:
    sessions=_request("GET","/sessions")
    if not isinstance(sessions,list) or not sessions:
        raise RuntimeError("No active browser session")
    return sessions[0]["id"]

def _url()->str:
    return str(_request("GET",f"/session/{_sid()}/url") or "")

def _text(limit=12000)->str:
    sid=_sid()
    script="return document && document.body ? document.body.innerText : '';"
    return str(_request("POST",f"/session/{sid}/execute/sync",{"script":script,"args":[]}) or "")[:limit]

def _navigate(url:str):
    if not re.match(r"^https?://",url):
        raise ValueError("Only HTTP(S) URLs are allowed")
    sid=_sid(); _request("POST",f"/session/{sid}/url",{"url":url})
    return {"url":_url()}

def _refresh():
    sid=_sid(); _request("POST",f"/session/{sid}/refresh"); return {"url":_url()}

def _click_text(text:str):
    sid=_sid()
    script="""const wanted=arguments[0];
const els=[...document.querySelectorAll('button,a,[role="button"],input[type="submit"],label')];
const el=els.find(e=>(e.innerText||e.value||e.getAttribute('aria-label')||'').trim()===wanted);
if(!el) return false; el.click(); return true;"""
    ok=_request("POST",f"/session/{sid}/execute/sync",{"script":script,"args":[text]})
    if not ok: raise RuntimeError(f"Visible exact text not found: {text}")
    return {"url":_url()}

def _fill(label:str,value:str):
    if re.search(r"password|passcode|otp|one[- ]?time|verification|security|secret|token",label,re.I):
        raise PermissionError("Sensitive authentication fields require human approval and are never automated by this runtime.")
    sid=_sid()
    script="""const label=arguments[0], val=arguments[1];
const labels=[...document.querySelectorAll('label')];
let el=labels.find(x=>(x.innerText||'').trim()===label);
let target=el ? (el.control || (el.htmlFor && document.getElementById(el.htmlFor))) : null;
if(!target) target=[...document.querySelectorAll('input,textarea,[contenteditable="true"]')].find(x=>
 (x.getAttribute('aria-label')||x.getAttribute('name')||x.getAttribute('placeholder')||'').trim()===label);
if(!target) return false;
target.focus(); target.value=val; target.dispatchEvent(new Event('input',{bubbles:true}));
target.dispatchEvent(new Event('change',{bubbles:true})); return true;"""
    ok=_request("POST",f"/session/{sid}/execute/sync",{"script":script,"args":[label,value]})
    if not ok: raise RuntimeError(f"Field not found: {label}")
    return {"url":_url()}

def _plan(goal:str, steps:Any|None)->list[dict[str,Any]]:
    if steps is not None:
        if not isinstance(steps,list) or len(steps)>MAX_STEPS:
            raise ValueError(f"steps must be a list of at most {MAX_STEPS} items")
        return steps
    urls=re.findall(r"https?://[^\s]+",goal)
    if urls:
        return [{"action":"navigate","url":urls[0].rstrip(".,)")},{"action":"verify","contains_url":urls[0].rstrip(".,)") }]
    return [{"action":"snapshot"}]

def _verify(step:dict[str,Any],result:Any)->dict[str,Any]:
    if "contains_url" in step:
        expected=str(step["contains_url"])
        actual=_url()
        return {"passed":expected in actual,"expected_url":expected,"actual_url":actual}
    return {"passed":True}

class AgentManager:
    def __init__(self):
        self.tasks:dict[str,dict[str,Any]]={}
        self.lock=threading.RLock()

    def start(self,goal:str,steps:Any|None=None)->dict[str,Any]:
        task_id=uuid.uuid4().hex[:12]
        plan=_plan(goal,steps)
        task={"id":task_id,"goal":goal,"status":"queued","plan":plan,"step_index":0,
              "results":[],"error":None,"approval_required":None,"created_at":int(time.time())}
        with self.lock: self.tasks[task_id]=task
        threading.Thread(target=self._run,args=(task_id,),daemon=True).start()
        return self.public(task)

    def _run(self,task_id:str):
        with self.lock: self.tasks[task_id]["status"]="running"
        while True:
            with self.lock:
                t=self.tasks.get(task_id)
                if not t or t["status"] in {"stopped","failed","completed"}: return
                if t["status"]=="paused": return
                i=t["step_index"]; plan=t["plan"]
            if i>=len(plan):
                with self.lock: t["status"]="completed"
                return
            step=plan[i]
            try:
                action=step.get("action")
                if action=="approval":
                    with self.lock:
                        t["status"]="paused"; t["approval_required"]=step.get("reason","Human approval required")
                    return
                if action=="snapshot":
                    result={"text":_text()}
                elif action=="navigate":
                    result=_navigate(str(step["url"]))
                elif action=="refresh":
                    result=_refresh()
                elif action=="click_text":
                    result=_click_text(str(step["text"]))
                elif action=="fill_label":
                    result=_fill(str(step["label"]),str(step.get("value","")))
                elif action=="wait":
                    time.sleep(min(30,max(0,float(step.get("seconds",1)))))
                    result={"waited":step.get("seconds",1)}
                elif action=="verify":
                    result=_verify(step,{})
                else:
                    raise ValueError(f"Unsupported action: {action}")
                verification=_verify(step,result)
                if not verification.get("passed",True):
                    raise RuntimeError(f"Verifier failed: {verification}")
                with self.lock:
                    t["results"].append({"step":i,"action":action,"result":result,"verification":verification})
                    t["step_index"]=i+1
            except PermissionError as exc:
                with self.lock:
                    t["status"]="paused"; t["approval_required"]=str(exc)
                return
            except Exception as exc:
                with self.lock:
                    t["status"]="failed"; t["error"]=str(exc)
                return

    def approve(self,task_id:str)->dict[str,Any]:
        with self.lock:
            t=self.tasks.get(task_id)
            if not t: raise KeyError("Unknown task")
            if t["status"]!="paused": raise ValueError("Task is not awaiting approval")
            t["approval_required"]=None; t["status"]="queued"
        threading.Thread(target=self._run,args=(task_id,),daemon=True).start()
        return self.public(t)

    def pause(self,task_id:str)->dict[str,Any]:
        with self.lock:
            t=self.tasks.get(task_id)
            if not t: raise KeyError("Unknown task")
            if t["status"]=="running": t["status"]="paused"
            return self.public(t)

    def stop(self,task_id:str)->dict[str,Any]:
        with self.lock:
            t=self.tasks.get(task_id)
            if not t: raise KeyError("Unknown task")
            t["status"]="stopped"; t["approval_required"]=None
            return self.public(t)

    def status(self,task_id:str)->dict[str,Any]:
        with self.lock:
            t=self.tasks.get(task_id)
            if not t: raise KeyError("Unknown task")
            return self.public(t)

    @staticmethod
    def public(t:dict[str,Any])->dict[str,Any]:
        return {k:v for k,v in t.items() if k!="lock"}

manager=AgentManager()
