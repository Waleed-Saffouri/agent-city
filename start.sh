#!/bin/bash
# One-step start for Agent City on a Mac or Linux machine.
#   ./start.sh            set up (first time only), start the city, open it in your browser
# The morning routine then runs by itself at 7:00 while this window stays open.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "First run: setting up (about a minute)..."
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
  ./.venv/bin/python run.py protect . >/dev/null || true
fi
[ -f profile/cv.md ] || echo "Tip: copy your CV to profile/cv.md so the Writer can draft cover letters."
command -v gitleaks >/dev/null || echo "Tip: 'brew install gitleaks' gives Sentinel deeper secret scanning."
command -v claude >/dev/null || echo "Tip: install Claude Code and log in so the agents can think (otherwise they use simple rules)."

FIRST=0; [ -f data/city.db ] || FIRST=1
./.venv/bin/python run.py schedule &   # 7:00 morning routine
SCHED=$!
trap 'kill $SCHED 2>/dev/null' EXIT

( sleep 2; (open http://localhost:8000 2>/dev/null || xdg-open http://localhost:8000 2>/dev/null) ) &
# First time ever: run the routine now so the city has something to show.
[ $FIRST = 1 ] && ( sleep 3; ./.venv/bin/python run.py morning >/dev/null 2>&1 ) &
echo "Agent City is running at http://localhost:8000  (press Control+C to stop)"
./.venv/bin/python -m uvicorn server:app --port 8000 --log-level warning
