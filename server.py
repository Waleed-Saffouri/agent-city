"""The city's web server: serves the pixel city and lets it talk to the agents.

    uvicorn server:app --reload     then open http://localhost:8000
"""
import json
import threading

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db
from agents.job_scout import JobScout
from agents.manager import Manager
from agents.sentinel import Sentinel
from agents.writer import Writer
from config import DAILY_TOKEN_BUDGET, ROOT

AGENTS = {"scout": JobScout, "sentinel": Sentinel, "writer": Writer, "manager": Manager}
app = FastAPI(title="Agent City")
db.init()


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/state")
def state():
    states = {r["agent"]: r for r in db.rows("SELECT * FROM agent_state")}
    agents = []
    for name, cls in AGENTS.items():
        s = states.get(name, {})
        agents.append({"name": name, "role": cls.role, "tier": cls.tier,
                       "status": s.get("status", "idle"),
                       "last_result": json.loads(s["last_result"]) if s.get("last_result") else None})
    jobs = (db.rows("SELECT * FROM jobs WHERE status='pending' AND track='internship' ORDER BY score DESC LIMIT 25")
            + db.rows("SELECT * FROM jobs WHERE status='pending' AND track='fulltime' ORDER BY score DESC LIMIT 25"))
    for j in jobs:
        j["reasons"] = json.loads(j["reasons"] or "[]")
    brief = db.rows("SELECT * FROM briefs ORDER BY ts DESC LIMIT 1")
    return {
        "agents": agents,
        "jobs": jobs,
        "counts": {s["status"]: s["n"] for s in db.rows("SELECT status, COUNT(*) n FROM jobs GROUP BY status")},
        "brief": brief[0]["text"] if brief else None,
        "messages": db.rows("SELECT * FROM messages ORDER BY ts DESC LIMIT 15"),
        "activity": db.rows("SELECT * FROM activity ORDER BY ts DESC LIMIT 30"),
        "tokens": {"used": db.tokens_today(), "budget": DAILY_TOKEN_BUDGET},
        "drafts": db.rows("""SELECT d.*, j.company, j.title, j.url FROM drafts d JOIN jobs j ON j.id = d.job_id
                             ORDER BY d.ts DESC LIMIT 20"""),
        "approved_waiting": db.rows("""SELECT j.id, j.company, j.title FROM jobs j LEFT JOIN drafts d ON d.job_id = j.id
                                       WHERE j.status='approved' AND d.job_id IS NULL"""),
        "findings": db.rows("""SELECT * FROM findings WHERE status='open' ORDER BY
                               CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, ts DESC LIMIT 30"""),
    }


@app.post("/api/run/{name}")
def run_agent(name: str):
    if name == "morning":
        def go():
            JobScout().run(); Sentinel().run(); Writer().run(); Manager().run()
    else:
        go = AGENTS[name]().run
    threading.Thread(target=go, daemon=True).start()
    return {"started": name}


@app.post("/api/jobs/{job_id}/{action}")
def job_action(job_id: str, action: str):
    status = {"approve": "approved", "skip": "skipped", "applied": "applied"}[action]
    db.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    job = db.rows("SELECT company, title FROM jobs WHERE id=?", (job_id,))
    if job:
        db.log("you", "decision", f"{status.title()}: {job[0]['company']} – {job[0]['title']}")
        if status == "approved":
            db.send("you", "writer", f"Approved {job[0]['company']}. Please draft the application.")
            threading.Thread(target=Writer().run, daemon=True).start()
    return {"ok": True}


@app.get("/drafts/{job_id}")
def draft_letter(job_id: str):
    d = db.rows("SELECT folder FROM drafts WHERE job_id=?", (job_id,))
    if not d:
        raise HTTPException(404, "No draft for that job yet.")
    return FileResponse(Path(d[0]["folder"]) / "cover_letter.html")


@app.post("/api/findings/{fid}/dismiss")
def dismiss(fid: str):
    db.execute("UPDATE findings SET status='dismissed' WHERE id=?", (fid,))
    db.log("you", "decision", "Dismissed a security finding.")
    return {"ok": True}


class Chat(BaseModel):
    text: str


@app.post("/api/chat")
def chat(msg: Chat):
    db.log("you", "chat", msg.text)
    answer = Manager().chat(msg.text)
    db.log("manager", "chat", answer[:300])
    return {"answer": answer}
