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
from config import CANVAS_ICS_URL

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
        m = re.search(r"\[([^\]]+)\]\s*$", summary)
        course = re.sub(r"\s*\(.*?\)|-\d{3}.*$", "", m[1]).strip() if m else ""
        title = summary[:m.start()].strip() if m else summary
        events.append({"uid": fields.get("UID", ("", summary + raw))[1], "title": title, "course": course,
                       "due": due, "time": tm, "url": fields.get("URL", ("", ""))[1]})
    return events


class School(Agent):
    name = "school"
    tier = "worker"
    role = "Reads your Canvas calendar and adds assignments, quizzes and exams to your Planner."

    def upsert(self, uid, title, due, time_, priority):
        """Add a to-do, or move it if Canvas changed the date. Never re-adds one you finished."""
        row = db.rows("SELECT id, due, due_time, status FROM tasks WHERE source_uid=?", (uid,))
        if row:
            r = row[0]
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
        if not CANVAS_ICS_URL:
            self.log("Not connected to Canvas yet: add your Canvas calendar feed link as CANVAS_ICS_URL in .env.", "warn")
            return {"added": 0, "connected": False}
        events = parse_ics(self.fetch(CANVAS_ICS_URL, timeout=30, raw=True))
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
        week = sum(1 for e in events if today <= e["due"] <= today + timedelta(days=7))
        self.log(f"Read {len(events)} Canvas items: {week} due this week. Added {added} to-dos, moved {moved}.")
        self.send("planner", f"Canvas: {week} items due this week ({added} new, {moved} moved).")
        return {"added": added, "moved": moved, "this_week": week, "connected": True}


def canvas_host():
    return urllib.parse.urlparse(CANVAS_ICS_URL).hostname or ""
