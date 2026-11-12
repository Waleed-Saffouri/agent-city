"""The city's shared memory: one small SQLite database."""
import json
import sqlite3
import time
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (      -- work log: everything agents do
  id INTEGER PRIMARY KEY, ts REAL, agent TEXT, kind TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS messages (      -- the message board agents use to talk
  id INTEGER PRIMARY KEY, ts REAL, sender TEXT, recipient TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS jobs (          -- what the Job Scout finds
  id TEXT PRIMARY KEY, ts REAL, company TEXT, title TEXT, locations TEXT, url TEXT,
  source TEXT, posted REAL, score INTEGER, reasons TEXT,
  status TEXT DEFAULT 'pending',           -- pending -> approved / skipped / applied
  track TEXT DEFAULT 'fulltime',           -- 'internship' (Spring 2027) or 'fulltime' (after May 2027)
  salary TEXT DEFAULT ''                   -- pay range when the posting lists one
);
CREATE TABLE IF NOT EXISTS briefs (        -- the manager's morning briefs
  id INTEGER PRIMARY KEY, ts REAL, text TEXT
);
CREATE TABLE IF NOT EXISTS agent_state (   -- lets the city light up working buildings
  agent TEXT PRIMARY KEY, status TEXT, updated REAL, last_result TEXT
);
CREATE TABLE IF NOT EXISTS token_usage (
  id INTEGER PRIMARY KEY, ts REAL, agent TEXT, model TEXT, tokens INTEGER
);
CREATE TABLE IF NOT EXISTS notes (          -- your brain dumps, exactly as you typed them
  id INTEGER PRIMARY KEY, ts REAL, text TEXT, processed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tasks (          -- what the Planner pulled out of them
  id INTEGER PRIMARY KEY, ts REAL, title TEXT, kind TEXT DEFAULT 'task', due TEXT, due_time TEXT,
  priority TEXT DEFAULT 'normal', project TEXT DEFAULT '', status TEXT DEFAULT 'open',
  note_id INTEGER, reminded INTEGER DEFAULT 0, done_ts REAL
);
CREATE TABLE IF NOT EXISTS drafts (         -- what the Writer produced for approved jobs
  job_id TEXT PRIMARY KEY, ts REAL, folder TEXT, status TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS findings (       -- what Sentinel finds (secret values are never stored)
  id TEXT PRIMARY KEY, ts REAL, severity TEXT, check_name TEXT, target TEXT, location TEXT,
  detail TEXT, fix TEXT, status TEXT DEFAULT 'open'
);
"""


@contextmanager
def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}   # upgrade older databases
        if "track" not in cols:
            c.execute("ALTER TABLE jobs ADD COLUMN track TEXT DEFAULT 'fulltime'")
        if "salary" not in cols:
            c.execute("ALTER TABLE jobs ADD COLUMN salary TEXT DEFAULT ''")


def rows(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def execute(sql, args=()):
    """Run a write. Returns the new row's id for INSERTs."""
    with conn() as c:
        return c.execute(sql, args).lastrowid


def log(agent, kind, text):
    execute("INSERT INTO activity(ts, agent, kind, text) VALUES (?,?,?,?)", (time.time(), agent, kind, text))


def send(sender, recipient, text):
    execute("INSERT INTO messages(ts, sender, recipient, text) VALUES (?,?,?,?)",
            (time.time(), sender, recipient, text))
    log(sender, "message", f"→ {recipient}: {text}")


def set_state(agent, status, last_result=None):
    execute("""INSERT INTO agent_state(agent, status, updated, last_result) VALUES (?,?,?,?)
               ON CONFLICT(agent) DO UPDATE SET status=excluded.status, updated=excluded.updated,
               last_result=COALESCE(excluded.last_result, agent_state.last_result)""",
            (agent, status, time.time(), json.dumps(last_result) if last_result is not None else None))


def tokens_today():
    start = time.time() - (time.time() % 86400)
    r = rows("SELECT COALESCE(SUM(tokens),0) AS t FROM token_usage WHERE ts >= ?", (start,))
    return r[0]["t"]


def add_finding(severity, check, target, location, detail, fix):
    """Record a security finding (used by Sentinel, and by any agent that spots something)."""
    import hashlib
    key = hashlib.sha1("|".join([check, str(target), location, detail]).encode(), usedforsecurity=False).hexdigest()[:16]
    execute("""INSERT INTO findings(id, ts, severity, check_name, target, location, detail, fix)
               VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET ts=excluded.ts, status='open'""",
            (key, time.time(), severity, check, str(target), location, detail, fix))
    return key
