"""Run agents from the command line.

    python run.py serve        # everything: the city website + 7:00 routine + reminders + auto-update
                               # (this is what the auto-start installs; you normally never type it)
    python run.py morning      # run the daily routine once
    python run.py scout        # run one agent (scout, sentinel, writer, school, planner, manager)
    python run.py note "text"  # add a brain dump from the terminal; the Planner sorts it
    python run.py protect PATH # install Sentinel's pre-commit hook in a git repo (blocks leaked keys)
    python run.py schedule     # routine + reminders only, without the website
"""
import subprocess
import sys
import threading
import time
from datetime import datetime

import db
from agents.job_scout import JobScout
from agents.manager import Manager
from agents.planner import Planner
from agents.school import School
from agents.sentinel import Sentinel
from agents.writer import Writer
from config import ROOT

AGENTS = {"scout": JobScout, "sentinel": Sentinel, "writer": Writer, "school": School, "planner": Planner, "manager": Manager}
ORDER = ("scout", "sentinel", "writer", "school", "planner", "manager")   # manager last: the brief sees everything
MORNING_HOUR = 7
UPDATE_EVERY = 5 * 60   # check GitHub for a new version every 5 minutes
SCHOOL_EVERY = 2 * 60 * 60   # Canvas is checked every 2 hours (8am-10pm); it's free, no AI


def morning():
    for name in ORDER:
        try:
            AGENTS[name]().run()
        except Exception:
            pass   # the agent already logged it and told the Manager; keep the rest of the city going
    db.put("last_morning", datetime.now().date().isoformat())


def morning_due(now):
    """True at 7:00, and also later in the day if the Mac was asleep or off at 7:00 (catch-up)."""
    return now.hour >= MORNING_HOUR and db.get("last_morning") != now.date().isoformat()


def update_available():
    """Fast-forward to the newest version on GitHub. Returns True if new code was pulled."""
    def git(*a):
        return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True, timeout=120)
    if git("fetch", "-q", "origin").returncode != 0:
        return False
    behind = git("rev-list", "--count", "HEAD..@{u}").stdout.strip()
    if not behind or behind == "0":
        return False
    old_reqs = (ROOT / "requirements.txt").read_text()
    if git("pull", "-q", "--ff-only").returncode != 0:
        db.log("manager", "warn", "An update is available but couldn't be applied automatically (local changes?).")
        return False
    if (ROOT / "requirements.txt").read_text() != old_reqs:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)  # for the Applier
    db.log("manager", "info", f"Updated Agent City to the newest version ({behind} change(s)). Restarting.")
    return True


def head():
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def schedule(serve_web=False, auto_update=False):
    print(f"Agent City running. Morning routine at {MORNING_HOUR}:00 (or as soon as the Mac wakes up after that).")
    last_check = last_school = 0
    started_on = head()
    while True:
        now = datetime.now()
        if morning_due(now):
            morning()
            last_school = time.time()
        elif 8 <= now.hour < 22 and time.time() - last_school > SCHOOL_EVERY:
            last_school = time.time()
            try:   # keep the Planner current with Canvas all day, not just at 7am
                School().run()
            except Exception:
                pass
        try:
            Planner().remind_due(now)   # timed reminders, checked every minute (no AI, free)
        except Exception as e:
            print(f"reminder check failed: {e}")
        if auto_update and time.time() - last_check > UPDATE_EVERY:
            last_check = time.time()
            try:
                # new code from GitHub, or code someone updated by hand: restart onto it
                if update_available() or head() != started_on:
                    sys.exit(0)   # the auto-start service restarts us on the new code
            except SystemExit:
                raise
            except Exception as e:
                print(f"update check failed: {e}")
        time.sleep(60)


def serve():
    import uvicorn
    server = uvicorn.Server(uvicorn.Config("server:app", host="127.0.0.1", port=8000, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    schedule(auto_update=True)


if __name__ == "__main__":
    db.init()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "morning"
    if cmd == "morning":
        morning()
    elif cmd == "serve":
        serve()
    elif cmd == "note":
        print(f"Added {Planner().capture(' '.join(sys.argv[2:]))} item(s).")
    elif cmd == "protect":
        from agents.sentinel import install_hook
        for repo in sys.argv[2:] or ["."]:
            print(f"{repo}: {install_hook(repo)}")
    elif cmd == "schedule":
        schedule()
    else:
        AGENTS[cmd]().run()
