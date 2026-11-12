"""Job Scout: every morning, find new jobs that fit Waleed and queue them for approval."""
import json
import time
import urllib.request

import db
from agents.base import Agent
from config import (AVOID_COMPANIES, JOB_SOURCES, MAX_AGE_DAYS, PREFERRED_LOCATIONS,
                    SKIP_TITLE, TITLE_KEYWORDS)


def score_job(job):
    """Rule-based fit score (0-100) with human-readable reasons."""
    title = job.get("title", "").lower()
    company = job.get("company_name", "").lower()
    locs = " | ".join(job.get("locations", [])).lower()
    reasons, score = [], 0

    if any(bad in company for bad in AVOID_COMPANIES):
        return 0, ["government/defense company (you said skip)"]
    if any(s in title for s in SKIP_TITLE):
        return 0, ["too senior"]

    best = max(((pts, kw) for kw, pts in TITLE_KEYWORDS.items() if kw in title), default=(0, None))
    if best[1]:
        score += best[0]
        reasons.append(f"title matches '{best[1].strip()}'")
    else:
        return 0, ["title not a data/analytics/finance role"]

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

    spons = (job.get("sponsorship") or "").lower()
    if "citizenship" in spons:
        score += 5; reasons.append("US citizens only (fewer applicants, you qualify)")

    return min(score, 100), reasons


class JobScout(Agent):
    name = "scout"
    tier = "worker"
    role = "Finds new jobs every morning and queues the best ones for your approval."

    def fetch(self, url):
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r)

    def work(self):
        known = {r["id"] for r in db.rows("SELECT id FROM jobs")}
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
                score, reasons = score_job(j)
                if score < 45:
                    continue
                new.append((jid, time.time(), j["company_name"], j["title"],
                            "; ".join(j.get("locations", []))[:200], j.get("url", ""),
                            source, j.get("date_posted", 0), score, json.dumps(reasons)))
        for row in new:
            db.execute("""INSERT OR IGNORE INTO jobs(id, ts, company, title, locations, url, source,
                          posted, score, reasons) VALUES (?,?,?,?,?,?,?,?,?,?)""", row)
        top = sorted(new, key=lambda r: -r[8])[:3]
        summary = ", ".join(f"{r[2]} ({r[8]})" for r in top) or "none"
        self.log(f"Checked {checked} fresh listings, queued {len(new)} good matches.")
        self.send("manager", f"Found {len(new)} new matches waiting for approval. Top: {summary}.")
        return {"checked": checked, "queued": len(new)}
