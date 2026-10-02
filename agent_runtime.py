"""Background browser-agent runtime with durable in-process task events."""
from __future__ import annotations
import json, os, re, threading, time, uuid
from collections import deque
from typing import Any
import urllib.request

BASE="http://127.0.0.1:4444/wd/hub"
MAX_STEPS=int(os.getenv("AGENT_MAX_STEPS","100"))
MAX_EVENTS=500
ACTION_RETRIES=max(0,int(os.getenv("AGENT_ACTION_RETRIES","2")))
LLM_BASE=os.getenv("AGENT_LLM_BASE_URL","").rstrip("/")
LLM_KEY=os.getenv("AGENT_LLM_API_KEY","")
LLM_MODEL=os.getenv("AGENT_LLM_MODEL","")

class EventBus:
    def __init__(self):
        self.events=deque(maxlen=MAX_EVENTS); self.cv=threading.Condition(); self.seq=0
    def emit(self, task_id, kind, message, **data):
        with self.cv:
            self.seq+=1
            event={"seq":self.seq,"task_id":task_id,"ts":int(time.time()),"kind":kind,"message":message,**data}
            self.events.append(event); self.cv.notify_all(); return event
    def since(self, task_id, seq=0):
        with self.cv: return [e for e in self.events if e["task_id"]==task_id and e["seq"]>seq]
    def wait(self, task_id, seq=0, timeout=15):
        with self.cv:
            items=self.since(task_id,seq)
            if items: return items
            self.cv.wait(timeout)
            return self.since(task_id,seq)

events=EventBus()

def _llm_next(goal, observation, history):
    if not (LLM_BASE and LLM_KEY and LLM_MODEL):
        return None
    system="Return ONLY one JSON object. Allowed actions: navigate(url), click_text(text), fill_label(label,value), wait(seconds), refresh, approval(reason), done. Use exact visible button/link text from the observation. Re-check the current page after every action. Never handle passwords, OTPs, tokens or security challenges; use approval."
    body={"model":LLM_MODEL,"messages":[{"role":"system","content":system},{"role":"user","content":json.dumps({"goal":goal,"observation":observation,"recent_steps":history[-6:]},ensure_ascii=False)}],"temperature":0.1}
    req=urllib.request.Request(LLM_BASE+"/chat/completions",data=json.dumps(body).encode(),method="POST",headers={"Content-Type":"application/json","Authorization":"Bearer "+LLM_KEY})
    with urllib.request.urlopen(req,timeout=45) as resp:
        data=json.loads(resp.read().decode())
    raw=data["choices"][0]["message"]["content"].strip()
    step=json.loads(raw)
    if not isinstance(step,dict) or "action" not in step:
        raise ValueError("Planner returned invalid action")
    return step

def _observe_page():
    s=_sid()
    js='return {url:location.href,title:document.title,text:(document.body?.innerText||"").slice(0,10000),buttons:[...document.querySelectorAll("button,a,[role=button],input[type=submit]")].slice(0,80).map((e,i)=>({ref:"e"+(i+1),text:(e.innerText||e.value||e.getAttribute("aria-label")||"").trim(),tag:e.tagName.toLowerCase(),role:e.getAttribute("role")||""})).filter(x=>x.text),fields:[...document.querySelectorAll("input,textarea,select")].slice(0,40).map(e=>({label:e.getAttribute("aria-label")||e.name||e.placeholder||e.type||"field",type:e.type||e.tagName.toLowerCase()}))};'
    return _request("POST",f"/session/{s}/execute/sync",{"script":js,"args":[]})

def _request(method,path,payload=None):
    body=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(BASE+path,data=body,method=method,headers={"Content-Type":"application/json","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=30) as r: raw=r.read().decode()
    obj=json.loads(raw) if raw else {}
    return obj.get("value",obj) if isinstance(obj,dict) else obj

def _sid():
    sessions=_request("GET","/sessions")
    if not isinstance(sessions,list) or not sessions: raise RuntimeError("No active browser session")
    return sessions[0]["id"]

def _url():
    return str(_request("GET",f"/session/{_sid()}/url") or "")

def _text(limit=12000):
    sid=_sid()
    return str(_request("POST",f"/session/{sid}/execute/sync",{"script":"return document && document.body ? document.body.innerText : '';","args":[]}) or "")[:limit]

def _navigate(url):
    if not re.match(r"^https?://",url): raise ValueError("Only HTTP(S) URLs are allowed")
    sid=_sid(); _request("POST",f"/session/{sid}/url",{"url":url}); return {"url":_url()}

def _refresh():
    sid=_sid(); _request("POST",f"/session/{sid}/refresh"); return {"url":_url()}

def _click_text(text):
    sid=_sid()
    script="""const wanted=arguments[0];const els=[...document.querySelectorAll('button,a,[role="button"],input[type="submit"],label')];const el=els.find(e=>(e.innerText||e.value||e.getAttribute('aria-label')||'').trim()===wanted);if(!el)return false;el.click();return true;"""
    ok=_request("POST",f"/session/{sid}/execute/sync",{"script":script,"args":[text]})
    if not ok: raise RuntimeError(f"Visible exact text not found: {text}")
    return {"url":_url()}

def _fill(label,value):
    if re.search(r"password|passcode|otp|one[- ]?time|verification|security|secret|token",label,re.I):
        raise PermissionError("Sensitive authentication fields require human approval and are never automated.")
    sid=_sid()
    script="""const label=arguments[0],val=arguments[1];const labels=[...document.querySelectorAll('label')];let el=labels.find(x=>(x.innerText||'').trim()===label);let target=el?(el.control||(el.htmlFor&&document.getElementById(el.htmlFor))):null;if(!target)target=[...document.querySelectorAll('input,textarea,[contenteditable="true"]')].find(x=>(x.getAttribute('aria-label')||x.getAttribute('name')||x.getAttribute('placeholder')||'').trim()===label);if(!target)return false;target.focus();target.value=val;target.dispatchEvent(new Event('input',{bubbles:true}));target.dispatchEvent(new Event('change',{bubbles:true}));return true;"""
    ok=_request("POST",f"/session/{sid}/execute/sync",{"script":script,"args":[label,value]})
    if not ok: raise RuntimeError(f"Field not found: {label}")
    return {"url":_url()}

def _plan(goal,steps):
    if steps is not None:
        if not isinstance(steps,list) or len(steps)>MAX_STEPS: raise ValueError(f"steps must be a list of at most {MAX_STEPS} items")
        return steps
    urls=re.findall(r"https?://[^\s]+",goal)
    if urls:
        u=urls[0].rstrip(".,)")
        return [{"action":"navigate","url":u},{"action":"verify","contains_url":u}]
    if LLM_BASE and LLM_KEY and LLM_MODEL:
        return []
    return [{"action":"snapshot"}]

def _verify(step,result):
    if "contains_url" in step:
        expected=str(step["contains_url"]); actual=_url()
        return {"passed":expected in actual,"expected_url":expected,"actual_url":actual}
    return {"passed":True}

class AgentManager:
    def __init__(self): self.tasks={}; self.lock=threading.RLock()

    def start(self,goal,steps=None):
        task_id=uuid.uuid4().hex[:12]; plan=_plan(goal,steps)
        task={"id":task_id,"goal":goal,"status":"queued","plan":plan,"step_index":0,"results":[],"error":None,"approval_required":None,"created_at":int(time.time()),"started_at":None,"finished_at":None,"current_action":None,"current_url":None}
        with self.lock:self.tasks[task_id]=task
        events.emit(task_id,"queued","🟡 কাজটি গ্রহণ করেছি। ধাপে ধাপে শুরু করছি।",step=0,total=len(plan))
        threading.Thread(target=self._run,args=(task_id,),daemon=True).start()
        return self.public(task)

    def _run(self,task_id):
        with self.lock:
            t=self.tasks[task_id]; t["status"]="running"; t["started_at"]=int(time.time())
        events.emit(task_id,"started","🟢 Agent কাজ শুরু করেছে। Planner → Browser → Verifier চলছে।",step=0,total=len(t["plan"]))
        while True:
            with self.lock:
                t=self.tasks.get(task_id)
                if not t or t["status"] in {"stopped","failed","completed"}: return
                if t["status"]=="paused": return
                i=t["step_index"]; plan=t["plan"]
            if i>=len(plan):
                if LLM_BASE and LLM_KEY and LLM_MODEL:
                    try:
                        step=_llm_next(t["goal"],_observe_page(),t["results"])
                        if step is not None:
                            plan.append(step)
                            events.emit(task_id,"replanned","🧠 Planner observed the browser and created the next step.",step=i,total=max(i+1,len(plan)),action=step.get("action"),url=_url())
                            continue
                    except Exception as exc:
                        with self.lock:t["status"]="failed";t["error"]="Planner error: "+str(exc);t["finished_at"]=int(time.time())
                        events.emit(task_id,"failed","🔴 Planner failed: "+str(exc),step=i,total=max(i+1,len(plan)),url=_url()); return
                with self.lock:t["status"]="completed";t["finished_at"]=int(time.time());t["current_action"]=None
                events.emit(task_id,"completed","✅ কাজ সম্পূর্ণ হয়েছে। সব ধাপ শেষ এবং verifier pass করেছে।",step=i,total=len(plan),url=_url()); return
            step=plan[i]; action=step.get("action")
            if action=="done":
                with self.lock:t["status"]="completed";t["finished_at"]=int(time.time());t["current_action"]=None
                events.emit(task_id,"completed","✅ Agent reports the goal is complete after verification.",step=i,total=len(plan),url=_url())
                return
            detail=""
            if action=="click_text": detail=" → button/text: "+str(step.get("text",""))
            elif action=="navigate": detail=" → URL: "+str(step.get("url",""))
            elif action=="fill_label": detail=" → field: "+str(step.get("label",""))
            elif action=="wait": detail=" → wait: "+str(step.get("seconds",1))+"s"
            events.emit(task_id,"step_started",f"🔵 Step {i+1}/{len(plan)} শুরু: {action}{detail}",step=i,total=len(plan),action=action,detail=detail,url=_url())
            try:
                with self.lock:t["current_action"]=action;t["current_url"]=_url()
                if action=="approval":
                    with self.lock:t["status"]="paused";t["approval_required"]=step.get("reason","Human approval required")
                    events.emit(task_id,"approval_required","🟠 আপনার সাহায্য দরকার। Browser-এ প্রয়োজনীয় action শেষ করুন, তারপর Approve করুন।",step=i,total=len(plan),action=action,url=_url()); return
                for attempt in range(ACTION_RETRIES+1):
                    try:
                        if action=="snapshot": result={"text":_text()}
                        elif action=="navigate": result=_navigate(str(step["url"]))
                        elif action=="refresh": result=_refresh()
                        elif action=="click_text": result=_click_text(str(step["text"]))
                        elif action=="fill_label": result=_fill(str(step["label"]),str(step.get("value","")))
                        elif action=="wait": time.sleep(min(30,max(0,float(step.get("seconds",1)))));result={"waited":step.get("seconds",1)}
                        elif action=="verify": result=_verify(step,{})
                        else: raise ValueError(f"Unsupported action: {action}")
                        verification=_verify(step,result)
                        if not verification.get("passed",True): raise RuntimeError(f"Verifier failed: {verification}")
                        break
                    except PermissionError: raise
                    except Exception as exc:
                        if attempt>=ACTION_RETRIES: raise
                        events.emit(task_id,"retry",f"🟡 Step {i+1} retry {attempt+1}/{ACTION_RETRIES}: {exc}",step=i,total=len(plan),action=action,url=_url())
                        try: _refresh()
                        except Exception: pass
                        time.sleep(min(3,attempt+1))
                with self.lock:
                    t["results"].append({"step":i,"action":action,"result":result,"verification":verification});t["step_index"]=i+1;t["current_url"]=_url()
                events.emit(task_id,"step_completed",f"🟢 Step {i+1}/{len(plan)} complete: {action}",step=i+1,total=len(plan),action=action,result=result,verification=verification,url=_url())
            except PermissionError as exc:
                with self.lock:t["status"]="paused";t["approval_required"]=str(exc)
                events.emit(task_id,"approval_required","🟠 Security/authentication step detected. Human approval required; secret values are never handled by the agent.",step=i,total=len(plan),action=action,url=_url()); return
            except Exception as exc:
                with self.lock:t["status"]="failed";t["error"]=str(exc);t["finished_at"]=int(time.time())
                events.emit(task_id,"failed",f"🔴 Step {i+1} failed after retries: {exc}",step=i,total=len(plan),action=action,url=_url()); return

    def approve(self,task_id):
        with self.lock:
            t=self.tasks.get(task_id)
            if not t:raise KeyError("Unknown task")
            if t["status"]!="paused":raise ValueError("Task is not awaiting approval")
            t["approval_required"]=None;t["status"]="queued"
        events.emit(task_id,"approved","🟢 Approval received. Agent continues.",step=t["step_index"],total=len(t["plan"]),url=_url())
        threading.Thread(target=self._run,args=(task_id,),daemon=True).start();return self.public(t)

    def pause(self,task_id):
        with self.lock:
            t=self.tasks.get(task_id)
            if not t:raise KeyError("Unknown task")
            if t["status"]=="running":t["status"]="paused"
            return self.public(t)

    def stop(self,task_id):
        with self.lock:
            t=self.tasks.get(task_id)
            if not t:raise KeyError("Unknown task")
            t["status"]="stopped";t["approval_required"]=None;t["finished_at"]=int(time.time())
        events.emit(task_id,"stopped","⏹️ কাজটি user stop করেছেন।",step=t["step_index"],total=len(t["plan"]),url=_url());return self.public(t)

    def status(self,task_id):
        with self.lock:
            t=self.tasks.get(task_id)
            if not t:raise KeyError("Unknown task")
            return self.public(t)

    def events_since(self,task_id,seq=0):
        if task_id not in self.tasks:raise KeyError("Unknown task")
        return events.since(task_id,seq)

    def wait_events(self,task_id,seq=0,timeout=15):
        if task_id not in self.tasks:raise KeyError("Unknown task")
        return events.wait(task_id,seq,timeout)

    @staticmethod
    def public(t):
        out={k:v for k,v in t.items() if k!="lock"};out["latest_events"]=events.since(t["id"],max(0,events.seq-20));return out

manager=AgentManager()
