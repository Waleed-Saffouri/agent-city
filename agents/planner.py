"""Planner: turns your brain dumps into a sorted to-do list with dates and reminders.

Type (or paste) anything, in any order: "need to do the ibm form before friday, also email
demetrius back, idea: add a trading tower". The Planner splits it into tasks, reminders and ideas,
works out dates ("friday", "the 19th", "tomorrow"), and files them under Today / This week / Later.
Things due show up in your morning brief, and timed reminders go to your phone if Telegram is set up.
"""
import json
import re
import time
from datetime import date, datetime, timedelta

import db
from agents.base import Agent

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
                                       "oct", "nov", "dec"], 1)}


def parse_due(text, today=None):
    """Find a date in plain English. Returns 'YYYY-MM-DD' or None."""
    today = today or date.today()
    t = text.lower()
    if re.search(r"\btoday|tonight\b", t):
        return today.isoformat()
    if "tomorrow" in t:
        return (today + timedelta(days=1)).isoformat()
    m = re.search(r"\b(\d{1,2})/(\d{1,2})\b", t)
    if m:
        d = date(today.year, int(m[1]), int(m[2]))
        return (d if d >= today else d.replace(year=today.year + 1)).isoformat()
    m = re.search(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(\d{1,2})", t)
    if m:
        d = date(today.year, MONTHS[m[1]], int(m[2]))
        return (d if d >= today else d.replace(year=today.year + 1)).isoformat()
    m = re.search(r"\bthe (\d{1,2})(?:st|nd|rd|th)\b|\b(\d{1,2})(?:st|nd|rd|th)\b", t)
    if m:
        day = int(m[1] or m[2])
        d = today.replace(day=min(day, 28)) if day > 28 else today.replace(day=day)
        if d < today:  # "the 3rd" said on the 12th means next month
            d = (today.replace(day=1) + timedelta(days=32)).replace(day=day if day <= 28 else 28)
        return d.isoformat()
    for i, wd in enumerate(WEEKDAYS):
        if re.search(rf"\b{wd}\b", t):
            ahead = (i - today.weekday()) % 7 or 7
            return (today + timedelta(days=ahead)).isoformat()
    if re.search(r"\bnext week\b", t):
        return (today + timedelta(days=7 - today.weekday())).isoformat()
    return None


def rule_split(text):
    """No-AI fallback: one item per line or sentence, dates found by parse_due."""
    parts = [p.strip(" -•*\t") for p in re.split(r"[\n;]|(?<=[.!?])\s+|\balso\b|\band then\b", text) if p and p.strip()]
    items = []
    for p in parts:
        if len(p) < 4:
            continue
        kind = "idea" if re.match(r"(?i)idea[:\s]|what if|maybe we", p) else "task"
        items.append({"title": p[:140], "kind": kind, "due": parse_due(p), "time": None,
                      "priority": "high" if re.search(r"(?i)urgent|asap|important|deadline", p) else "normal",
                      "project": "IBM" if re.search(r"(?i)\bibm\b", p) else ""})
    return items


def clean_items(raw):
    """Keep only well-formed items, whatever the AI returned."""
    out = []
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict) or not str(it.get("title", "")).strip():
            continue
        due = it.get("due")
        try:
            due = date.fromisoformat(due).isoformat() if due else None
        except (TypeError, ValueError):
            due = None
        tm = it.get("time")
        tm = tm if isinstance(tm, str) and re.fullmatch(r"\d{2}:\d{2}", tm) else None
        out.append({"title": str(it["title"]).strip()[:140],
                    "kind": it.get("kind") if it.get("kind") in ("task", "reminder", "idea") else "task",
                    "due": due, "time": tm,
                    "priority": "high" if it.get("priority") == "high" else "normal",
                    "project": str(it.get("project") or "")[:30]})
    return out


class Planner(Agent):
    name = "planner"
    tier = "manager"
    role = "Turns your brain dumps into a sorted to-do list with dates, and reminds you."

    def split(self, text):
        today = date.today()
        raw = self.think_json(
            f"Today is {today:%A, %B %d, %Y}. Waleed wrote this brain dump. Split it into separate items.\n"
            "Return ONLY a JSON list. Each item: {\"title\": short imperative to-do in his words, "
            "\"kind\": \"task\"|\"reminder\"|\"idea\", \"due\": \"YYYY-MM-DD\" or null (work out "
            "words like friday / the 19th / next week from today's date), \"time\": \"HH:MM\" or null, "
            "\"priority\": \"high\"|\"normal\", \"project\": short tag like IBM, School, Agent City, "
            "Trading bot, or \"\"}. Don't invent tasks he didn't mention. Split combined thoughts.\n\n"
            f"BRAIN DUMP:\n{text}", opener="[", max_tokens=1500)
        items = clean_items(raw) if raw is not None else []
        return (items, "ai") if items else (rule_split(text), "rules")

    def capture(self, text):
        """Save a brain dump and turn it into tasks right away."""
        note_id = db.execute("INSERT INTO notes(ts, text) VALUES (?,?)", (time.time(), text))
        db.set_state(self.name, "working")
        try:
            items, how = self.split(text)
            open_titles = {r["title"].lower() for r in db.rows("SELECT title FROM tasks WHERE status='open'")}
            added = 0
            for it in items:
                if it["title"].lower() in open_titles:
                    continue
                db.execute("""INSERT INTO tasks(ts, title, kind, due, due_time, priority, project, note_id)
                              VALUES (?,?,?,?,?,?,?,?)""",
                           (time.time(), it["title"], it["kind"], it["due"], it["time"], it["priority"],
                            it["project"], note_id))
                added += 1
            db.execute("UPDATE notes SET processed=1 WHERE id=?", (note_id,))
            self.log(f"Sorted your note into {added} item{'s' if added != 1 else ''}"
                     + (" (simple rules; AI unavailable)." if how == "rules" else "."))
            db.set_state(self.name, "idle", {"added": added})
            return added
        except Exception as e:
            db.set_state(self.name, "error", {"error": str(e)})
            self.log(f"Couldn't sort that note: {e}", "error")
            raise

    def due_summary(self):
        today = date.today().isoformat()
        week = (date.today() + timedelta(days=7)).isoformat()
        q = "SELECT title, due, project FROM tasks WHERE status='open' AND kind!='idea' AND due {} ORDER BY due"
        return {"overdue": db.rows(q.format("< ?"), (today,)),
                "today": db.rows(q.format("= ?"), (today,)),
                "this_week": db.rows(q.format("> ? AND due <= ?"), (today, week))}

    def remind_due(self, now=None):
        """Called every minute by the scheduler: sends timed reminders that are due. No AI, free."""
        now = now or datetime.now()
        rows = db.rows("""SELECT id, title FROM tasks WHERE status='open' AND reminded=0 AND due=? AND due_time IS NOT NULL
                          AND due_time <= ?""", (now.date().isoformat(), now.strftime("%H:%M")))
        for r in rows:
            self.notify(f"Reminder: {r['title']}")
            db.send(self.name, "you", f"Reminder: {r['title']}")
            db.execute("UPDATE tasks SET reminded=1 WHERE id=?", (r["id"],))
        return len(rows)

    def work(self):
        for n in db.rows("SELECT id, text FROM notes WHERE processed=0"):
            db.execute("DELETE FROM notes WHERE id=?", (n["id"],))
            self.capture(n["text"])
        s = self.due_summary()
        parts = []
        if s["overdue"]:
            parts.append(f"{len(s['overdue'])} overdue")
        if s["today"]:
            parts.append(f"{len(s['today'])} due today: " + "; ".join(t["title"] for t in s["today"][:3]))
        if s["this_week"]:
            parts.append(f"{len(s['this_week'])} more this week")
        msg = ", ".join(parts) or "Nothing due this week."
        self.send("manager", f"To-dos: {msg}")
        if s["today"] or s["overdue"]:
            self.notify("Due today: " + "; ".join(t["title"] for t in (s["overdue"] + s["today"])[:5]))
        return {"overdue": len(s["overdue"]), "today": len(s["today"]), "week": len(s["this_week"])}
