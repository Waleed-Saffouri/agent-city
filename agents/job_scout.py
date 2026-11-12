"""Job Scout: every morning, find new jobs that fit Waleed and queue them for approval.

Three layers, cheapest first:
  1. Rules (free): filters and a points score with plain-English reasons.
  2. Learning (free): titles similar to jobs you skipped lose points; similar to approved gain.
  3. AI re-rank (one cheap call a day): the top matches are re-checked against your profile.
"""
import json
import re
import time
import urllib.request
from collections import Counter

import db
from agents.base import Agent
from config import (AVOID_COMPANIES, GOOD_CATEGORIES, INTERNSHIP_PENALTY, JOB_SOURCES,
                    MAX_AGE_DAYS, OFF_TOPIC, PREFERRED_LOCATIONS, SKIP_TITLE, TITLE_KEYWORDS)

PROFILE = ("Waleed: Computational & Data Sciences B.S. (GMU, May 2027) with a finance minor. "
           "Skills: Python, SQL, pandas, statistics, Power BI, AWS basics, an algorithmic trading bot. "
           "Wants: data analyst / data science / analytics / finance-analytics roles, private sector, "
           "hybrid, NYC/VA-DC/Texas/NC/remote/London. Avoids: government, defense, cleared roles, "
           "lab or clinical research, hardware, roles needing a master's or PhD.")

STOP = {"and", "the", "of", "a", "-", "&", "intern", "analyst", "data", "i", "ii", "1", "2", "2027",
        "new", "grad", "associate", "junior", "entry", "level", "–", "summer", "full", "time"}


def words(title):
    return {w for w in re.findall(r"[a-z]+", title.lower()) if w not in STOP and len(w) > 2}


def already_applied():
    """Companies already in your career-ops tracker, so the Scout doesn't suggest them again."""
    from config import TRACKER_PATH
    if not TRACKER_PATH.exists():
        return set()
    names = set()
    for line in TRACKER_PATH.read_text().splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 3 and cells[1].isdigit():
            names.add(cells[3].lower().split(" (")[0])
    return names


def taste():
    """Learn from your ✓/✕ clicks: which title words you tend to approve or skip."""
    liked, disliked = Counter(), Counter()
    for r in db.rows("SELECT title, status FROM jobs WHERE status IN ('approved','applied','skipped')"):
        (disliked if r["status"] == "skipped" else liked).update(words(r["title"]))
    return liked, disliked


def score_job(job, source="", liked=None, disliked=None):
    """Rule-based fit score (0-100) with human-readable reasons."""
    title = job.get("title", "").lower()
    company = job.get("company_name", "").lower()
    locs = " | ".join(job.get("locations", [])).lower()
    reasons, score = [], 0

    if any(bad in company for bad in AVOID_COMPANIES):
        return 0, ["government/defense company (you said skip)"]
    if any(s in title for s in SKIP_TITLE):
        return 0, ["too senior or wrong type"]
    degrees = [d.lower() for d in job.get("degrees") or []]
    if degrees and not any("bachelor" in d for d in degrees):
        return 0, ["needs a master's/PhD"]

    best = max(((pts, kw) for kw, pts in TITLE_KEYWORDS.items() if kw in title), default=(0, None))
    if not best[1]:
        return 0, ["title not a data/analytics/finance role"]
    score += best[0]
    reasons.append(f"title matches '{best[1].strip()}'")

    off = sum(pts for kw, pts in OFF_TOPIC.items() if kw in title)
    if off:
        score -= off
        reasons.append("off-topic field (lab/clinical/hardware)")

    cat = GOOD_CATEGORIES.get(job.get("category", ""), 0)
    if cat:
        score += cat
        reasons.append(f"{job['category']} category")

    loc_pts = max((pts for kw, pts in PREFERRED_LOCATIONS.items() if kw in locs), default=0)
    if loc_pts:
        score += loc_pts
        reasons.append("preferred location")

    age_days = (time.time() - job.get("date_posted", 0)) / 86400
    if age_days <= 3:
        score += 20; reasons.append("posted in the last 3 days")
    elif age_days <= 7:
        score += 12; reasons.append("posted this week")
    else:
        score += 4

    if source == "internships" and not re.search(r"spring|winter|co-?op|fall", title):
        score -= INTERNSHIP_PENALTY
        reasons.append("summer internship (you graduate in May)")

    if liked or disliked:
        w = words(title)
        nudge = sum(min(liked[x], 3) for x in w) * 2 - sum(min(disliked[x], 3) for x in w) * 2
        nudge = max(-15, min(15, nudge))
        if nudge:
            score += nudge
            reasons.append("similar to jobs you approved" if nudge > 0 else "similar to jobs you skipped")

    return max(0, min(score, 100)), reasons


class JobScout(Agent):
    name = "scout"
    tier = "worker"
    role = "Finds new jobs every morning and queues the best ones for your approval."

    def fetch(self, url):
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r)

    def ai_rerank(self, rows):
        """One AI call for the whole batch (not one per job) to keep usage tiny."""
        top = sorted(rows, key=lambda r: -r[8])[:25]
        if not top:
            return rows
        listing = "\n".join(f"{i}. {r[2]} | {r[3]} | {r[4][:60]}" for i, r in enumerate(top))
        answer = self.think(
            f"{PROFILE}\n\nRate how well each job fits him from -20 (bad fit) to +20 (great fit). "
            f"Reply with ONLY a JSON object mapping the number to [adjustment, 'short reason'].\n\n{listing}",
            max_tokens=1500)
        if not answer:
            return rows
        try:
            adj = json.loads(answer[answer.index("{"):answer.rindex("}") + 1])
        except ValueError:
            self.log("AI re-rank reply wasn't readable; kept rule scores.", "warn")
            return rows
        out = {r[0]: r for r in rows}
        for i, r in enumerate(top):
            a = adj.get(str(i))
            if isinstance(a, list) and len(a) == 2:
                delta = max(-20, min(20, int(a[0])))
                reasons = json.loads(r[9]) + [f"AI: {a[1]}"]
                out[r[0]] = r[:8] + (max(0, min(100, r[8] + delta)), json.dumps(reasons))
        self.log(f"AI re-checked the top {len(top)} matches.")
        return list(out.values())

    def work(self):
        known = {r["id"] for r in db.rows("SELECT id FROM jobs")}
        liked, disliked = taste()
        applied = already_applied()
        new, checked = [], 0
        cutoff = time.time() - MAX_AGE_DAYS * 86400
        for source, url in JOB_SOURCES.items():
            try:
                listings = self.fetch(url)
            except Exception as e:
                self.log(f"Couldn't read {source} list: {e}", "error")
                continue
            for j in listings:
                if not j.get("active") or not j.get("is_visible", True) or j.get("date_posted", 0) < cutoff:
                    continue
                checked += 1
                jid = str(j.get("id"))
                if jid in known:
                    continue
                if any(a and (a in j["company_name"].lower() or j["company_name"].lower() in a)
                       for a in applied):
                    continue  # you've already applied there
                score, reasons = score_job(j, source, liked, disliked)
                if score < 45:
                    continue
                new.append((jid, time.time(), j["company_name"], j["title"],
                            "; ".join(j.get("locations", []))[:200], j.get("url", ""),
                            source, j.get("date_posted", 0), score, json.dumps(reasons)))
        new = self.ai_rerank(new)
        for row in new:
            db.execute("""INSERT OR IGNORE INTO jobs(id, ts, company, title, locations, url, source,
                          posted, score, reasons) VALUES (?,?,?,?,?,?,?,?,?,?)""", row)
        top = sorted(new, key=lambda r: -r[8])[:3]
        summary = ", ".join(f"{r[2]} ({r[8]})" for r in top) or "none"
        self.log(f"Checked {checked} fresh listings, queued {len(new)} good matches.")
        self.send("manager", f"Found {len(new)} new matches waiting for approval. Top: {summary}.")
        return {"checked": checked, "queued": len(new)}
