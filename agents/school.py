"""School: reads your Canvas calendar and puts every assignment, quiz and exam into the Planner.

It uses Canvas's private calendar feed (Calendar → "Calendar Feed" link), which is read-only and
needs no password or API token. No AI involved, so it's free.

  * Every assignment due in the next 3 weeks becomes a to-do with its real due date.
  * Exams, midterms, finals and quizzes also get a "start studying" to-do a few days earlier.
  * If a professor moves a due date, the to-do moves with it.
"""
import re
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import db
from agents.base import Agent
from config import CANVAS_ICS_URL, ROOT

SYLLABUS_PATH = ROOT / "profile" / "syllabus.json"   # dates pulled from each class's syllabus (private)
SYLLABUS_LOOKAHEAD = 45   # syllabus dates are early warnings, so look further ahead

LOOKAHEAD_DAYS = 21
EXAM = re.compile(r"(?i)\b(final exam|midterm|exam|test|final)\b")
QUIZ = re.compile(r"(?i)\bquiz\b")
BIG = re.compile(r"(?i)\b(project|paper|report|presentation|capstone|essay)\b")


def unfold(text):
    """ICS wraps long lines; a line starting with a space continues the previous one."""
    return re.sub(r"\r?\n[ \t]", "", text)


def parse_ics(text):
    """Return a list of {uid, title, course, due (date), time ("HH:MM" or None), url}."""
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfold(text or ""), re.S):
        fields = {}
        for line in block.strip().splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                fields.setdefault(key.split(";")[0].upper(), (key, val.strip()))
        if "SUMMARY" not in fields or "DTSTART" not in fields:
            continue
        key, raw = fields["DTSTART"]
        try:
            if "VALUE=DATE" in key or len(raw) == 8:
                due, tm = datetime.strptime(raw[:8], "%Y%m%d").date(), None
            else:
                dt = datetime.strptime(raw.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
                if raw.endswith("Z"):
                    dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
                due, tm = dt.date(), dt.strftime("%H:%M")
        except ValueError:
            continue
        summary = fields["SUMMARY"][1].replace("\\,", ",").replace("\\;", ";")
        m = re.search(r"\[(.+)\]\s*$", summary)          # "Homework 6 [Fall 2026 Linear Algebra (MATH-203-006)]"
        bracket = m[1] if m else ""
        code = re.search(r"\b([A-Z]{2,4})[- ]?(\d{3})\b", bracket or summary)
        course = f"{code[1]} {code[2]}" if code else bracket.strip()
        title = summary[:m.start()].strip() if m else summary
        events.append({"uid": fields.get("UID", ("", summary + raw))[1], "title": title, "course": course,
                       "due": due, "time": tm, "url": fields.get("URL", ("", ""))[1]})
    # Canvas lists an assignment twice when your section has its own date ("override"). The
    # override is the date that applies to you, so drop the general copy.
    overridden = {(e["course"], e["title"]) for e in events if "override" in e["uid"]}
    return [e for e in events if "override" in e["uid"] or (e["course"], e["title"]) not in overridden]


def load_syllabus():
    """Dated items from your syllabi: [{course, title, due: YYYY-MM-DD, kind}]. Missing file = none."""
    import json
    try:
        items = json.loads(SYLLABUS_PATH.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        try:
            out.append({**it, "due": date.fromisoformat(it["due"])})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def same_item(a_title, b_title):
    """Rough match between a syllabus line and a Canvas assignment ("Exam 2" vs "Midterm Exam 2")."""
    wa = set(re.findall(r"[a-z]+|\d+", a_title.lower())) - {"the", "a", "due", "and", "of"}
    wb = set(re.findall(r"[a-z]+|\d+", b_title.lower())) - {"the", "a", "due", "and", "of"}
    nums_a, nums_b = {w for w in wa if w.isdigit()}, {w for w in wb if w.isdigit()}
    if nums_a and nums_b and not nums_a & nums_b:
        return False
    return len(wa & wb) >= max(1, min(len(wa), len(wb)) // 2)


class School(Agent):
    name = "school"
    tier = "worker"
    role = "Reads your Canvas calendar and adds assignments, quizzes and exams to your Planner."

    def upsert(self, uid, title, due, time_, priority):
        """Add a to-do, or move it if Canvas changed the date. Never re-adds one you finished."""
        row = db.rows("SELECT id, due, due_time, status, title FROM tasks WHERE source_uid=?", (uid,))
        if row:
            r = row[0]
            if r["status"] == "open" and r["title"] != title and r["due"] == due:
                db.execute("UPDATE tasks SET title=? WHERE id=?", (title, r["id"]))   # cleaner name, same date
            if r["status"] == "open" and (r["due"] != due or r["due_time"] != time_):
                db.execute("UPDATE tasks SET due=?, due_time=?, title=? WHERE id=?", (due, time_, title, r["id"]))
                return "moved"
            return None
        # reminded=1: the due time is shown, but no text AT the deadline (too late to help); due
        # items come in the morning text instead.
        db.execute("""INSERT INTO tasks(ts, title, kind, due, due_time, priority, project, source_uid, reminded)
                      VALUES (strftime('%s','now'), ?, 'task', ?, ?, ?, 'School', ?, 1)""",
                   (title, due, time_, priority, uid))
        return "added"

    def work(self):
        if not CANVAS_ICS_URL and not SYLLABUS_PATH.exists():
            self.log("Not connected to Canvas yet: add your Canvas calendar feed link as CANVAS_ICS_URL in .env.", "warn")
            return {"added": 0, "connected": False}
        events = parse_ics(self.fetch(CANVAS_ICS_URL, timeout=30, raw=True)) if CANVAS_ICS_URL else []
        today, horizon = date.today(), date.today() + timedelta(days=LOOKAHEAD_DAYS)
        added = moved = 0
        for e in events:
            if not (today <= e["due"] <= horizon):
                continue
            label = f"{e['title']} ({e['course']})" if e["course"] else e["title"]
            prio = "high" if EXAM.search(e["title"]) or BIG.search(e["title"]) else "normal"
            res = self.upsert(e["uid"], f"Due: {label}", e["due"].isoformat(), e["time"], prio)
            added += res == "added"; moved += res == "moved"
            # study time before tests
            lead = 5 if EXAM.search(e["title"]) else 2 if QUIZ.search(e["title"]) else 0
            if lead:
                start = max(today, e["due"] - timedelta(days=lead))
                res = self.upsert(e["uid"] + "#study", f"Start studying: {label}", start.isoformat(), None, "high")
                added += res == "added"; moved += res == "moved"
        # Syllabus dates: professors often post assignments on Canvas late, so the syllabus is the
        # early warning. Skip a syllabus item once Canvas has the same thing within 3 days of it.
        syl_added = 0
        for it in load_syllabus():
            if not (today <= it["due"] <= today + timedelta(days=SYLLABUS_LOOKAHEAD)):
                continue
            if any(e["course"] and e["course"].replace(" ", "") in it["course"].replace(" ", "")
                   and abs((e["due"] - it["due"]).days) <= 3 and same_item(it["title"], e["title"]) for e in events):
                continue
            label = f"{it['title']} ({it['course']}, from syllabus)"
            kind = it.get("kind", "") or ("exam" if EXAM.search(it["title"]) else "")
            prio = "high" if kind in ("exam", "project", "paper") else "normal"
            uid = f"syl:{it['course']}:{it['title']}:{it['due']}"
            res = self.upsert(uid, f"Due: {label}", it["due"].isoformat(), None, prio)
            syl_added += res == "added"
            lead = 7 if kind == "exam" else 10 if kind in ("project", "paper") else 0
            if lead:
                start = max(today, it["due"] - timedelta(days=lead))
                verb = "Start studying" if lead == 7 else "Start working on"
                res = self.upsert(uid + "#start", f"{verb}: {label}", start.isoformat(), None, "high")
                syl_added += res == "added"
        added += syl_added
        if events:   # only when the feed was actually read
            live = {e["uid"] for e in events} | {e["uid"] + "#study" for e in events}
            for r in db.rows("SELECT id, source_uid FROM tasks WHERE status='open' AND source_uid LIKE 'event-%'"):
                if r["source_uid"] not in live:
                    db.execute("UPDATE tasks SET status='deleted' WHERE id=?", (r["id"],))
        week = sum(1 for e in events if today <= e["due"] <= today + timedelta(days=7))
        self.log(f"Read {len(events)} Canvas items: {week} due this week. Added {added} to-dos, moved {moved}.")
        self.send("planner", f"Canvas: {week} items due this week ({added} new, {moved} moved).")
        return {"added": added, "moved": moved, "this_week": week, "connected": True}


def canvas_host():
    return urllib.parse.urlparse(CANVAS_ICS_URL).hostname or ""
