"""Run agents from the command line.

    python run.py scout        # run one agent
    python run.py morning      # scout, sentinel, then manager (the daily routine)
    python run.py protect PATH # install Sentinel's pre-commit hook in a git repo (blocks leaked keys)
    python run.py schedule     # keep running and do the morning routine every day at 7:00
"""
import sys
import time
from datetime import datetime

import db
from agents.job_scout import JobScout
from agents.manager import Manager
from agents.sentinel import Sentinel

AGENTS = {"scout": JobScout, "sentinel": Sentinel, "manager": Manager}


def morning():
    for name in ("scout", "sentinel", "manager"):  # manager last, so the brief sees everything
        AGENTS[name]().run()


def schedule(hour=7):
    last = None
    print(f"Agent City scheduler running. Morning routine at {hour}:00 every day.")
    while True:
        now = datetime.now()
        if now.hour == hour and last != now.date():
            morning()
            last = now.date()
        time.sleep(60)


if __name__ == "__main__":
    db.init()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "morning"
    if cmd == "morning":
        morning()
    elif cmd == "protect":
        from agents.sentinel import install_hook
        for repo in sys.argv[2:] or ["."]:
            print(f"{repo}: {install_hook(repo)}")
    elif cmd == "schedule":
        schedule()
    else:
        AGENTS[cmd]().run()
