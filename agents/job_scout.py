"""Job Scout: every morning, find new jobs that fit Waleed and queue them for approval.

Three layers, cheapest first:
  1. Rules (free): filters and a points score with plain-English reasons.
  2. Learning (free): titles similar to jobs you skipped lose points; similar to approved gain.
  3. AI re-rank (one cheap call a day): the top matches are re-checked against your profile.
"""
import html
import json
import re
import time
import urllib.parse
from collections import Counter
from datetime import datetime

import db
from agents import guard
from agents.base import Agent
from config import (AVOID_COMPANIES, GOOD_CATEGORIES, GREENHOUSE_COMPANIES, INTERNSHIP_TERMS, JOB_SOURCES, SPRING_BONUS,
                    LOCATION_TIERS, OTHER_LOCATION_POINTS, DMV_PLACES,
                    MAX_AGE_DAYS, OFF_TOPIC, SKIP_TITLE, TITLE_KEYWORDS)

PROFILE = ("Waleed: Computational & Data Sciences B.S. (GMU, May 2027) with a finance minor. "
           "Skills: Python, SQL, pandas, statistics, Power BI, AWS basics, an algorithmic trading bot. "
           "Two goals: (1) a Spring 2027 internship, (2) a full-time job starting after May 2027. "
           "Wants: data analyst / data science / analytics / finance-analytics roles, private sector, "
           "hybrid, NYC/VA-DC/Texas/NC/remote/London. Avoids: government, defense, cleared roles, "
           "lab or clinical research, hardware, roles needing a master's or PhD.")

STOP = {"and", "the", "of", "a", "-", "&", "intern", "analyst", "data", "i", "ii", "1", "2", "2027",
        "new", "grad", "associate", "junior", "entry", "level", "–", "summer", "full", "time"}


US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
             "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()


def location_ok(locs):
    """True if any location is in the US, remote, or London (where you said you'd go)."""
    text = " | ".join(locs)
    text = re.sub(r"(?i)remote in (canada|uk|india|mexico|brazil|europe)[^|;]*", "", text)  # not US-remote
    if re.search(r"(?i)united states|\busa?\b|\bus-|remote|london|new york|nyc", text):
        return True
    return any(re.search(rf",\s*{st}\b", text) for st in US_STATES)


def from_greenhouse(slug, data):
    """Turn a Greenhouse board into the same shape as SimplifyJobs listings."""
    out = []
    for j in data.get("jobs", []):
        posted = j.get("first_published") or j.get("updated_at")
        try:
            ts = datetime.fromisoformat(posted).timestamp() if posted else 0
        except ValueError:
            ts = 0
        content = re.sub(r"<[^>]+>", " ", html.unescape(j.get("content") or ""))
        out.append({"salary": salary_from(content),
                    "id": f"gh-{slug}-{j['id']}", "company_name": (j.get("company_name") or slug).strip(),
                    "title": j.get("title", ""), "locations": [(j.get("location") or {}).get("name", "")],
                    "url": j.get("absolute_url", ""), "date_posted": ts, "active": True,
                    "is_visible": True, "category": "", "degrees": []})
    return out


INTERN_WORDS = re.compile(r"(?i)\bintern(ship)?\b|\bco-?op\b|\bextern\b")
SPRING_WORDS = re.compile(r"(?i)spring|winter|january|jan\b|off-?cycle|part-?time")


def track_of(job, source):
    """'internship' for a Spring 2027 internship, 'fulltime' for a job after May, None to skip."""
    title = job.get("title", "")
    terms = [t for t in (job.get("terms") or []) if t and t != "N/A"]
    is_intern = source == "internships" or bool(INTERN_WORDS.search(title))
    if not is_intern:
        return "fulltime"
    if terms:
        return "internship" if any(t in INTERNSHIP_TERMS for t in terms) else None
    return "internship" if SPRING_WORDS.search(title) else None   # untagged: needs a spring hint


SALARY = re.compile(
    r"\$\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s?[kK]|\d{2,3}(?:\.\d{1,2})?)"
    r"\s*(?:-|–|—|to)\s*\$?\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s?[kK]|\d{2,3}(?:\.\d{1,2})?)"
    r"(\s*(?:/|per|an)\s*(?:hour|hr|year|yr|annum))?")


def salary_from(text):
    """Pull a pay range like '$85,000 - $105,000' or '$25 - $30/hour' from posting text."""
    for m in SALARY.finditer(text or ""):
        lo, hi, unit = m.group(1), m.group(2), (m.group(3) or "").lower()
        def val(x):
            x = x.replace(",", "").strip()
            return float(x[:-1]) * 1000 if x[-1] in "kK" else float(x)
        a, b = val(lo), val(hi)
        if b < a:
            continue
        if "h" in unit or b < 200:            # hourly
            if 10 <= a <= 150:
                return f"${a:g}–${b:g}/hr"
        elif 30_000 <= a <= 600_000:
            return f"${a/1000:.0f}K–${b/1000:.0f}K"
    return ""


def salary_from_page(page):
    """Salary from a job page: the structured 'baseSalary' data many sites include, else the text."""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page or "", re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            base = (item or {}).get("baseSalary") if isinstance(item, dict) else None
            v = (base or {}).get("value") if isinstance(base, dict) else None
            if isinstance(v, dict) and v.get("minValue"):
                lo, hi = float(v["minValue"]), float(v.get("maxValue") or v["minValue"])
                if (v.get("unitText") or "").upper() == "HOUR" or hi < 200:
                    return f"${lo:g}–${hi:g}/hr"
                return f"${lo/1000:.0f}K–${hi/1000:.0f}K"
    text = re.sub(r"<[^>]+>", " ", html.unescape(page or ""))
    return salary_from(text)


def location_points(locs):
    """Points for the best location in a posting, and which one it was."""
    best, where = None, ""
    for loc in locs or []:
        if re.search(r"(?i)remote in (canada|uk|india|mexico|brazil|europe)", loc):
            continue   # remote, but only from another country
        low = loc.lower()
        pts = next((p for p, keys in sorted(LOCATION_TIERS.items(), reverse=True) if any(k in low for k in keys)),
                   OTHER_LOCATION_POINTS)
        if best is None or pts > best:
            best, where = pts, loc.split(",")[0].strip()
    return (best if best is not None else OTHER_LOCATION_POINTS), where


def commutable(locs):
    """Spring internships: somewhere you can get to from GMU, or fully remote."""
    for loc in locs or []:
        low = loc.lower()
        if any(k in low for k in DMV_PLACES):
            return True
        if "remote" in low and not re.search(r"(?i)remote in (canada|uk|india)|hybrid", low):
            return True
    return False


def fit_label(score):
    return "Great fit" if score >= 80 else "Good fit" if score >= 65 else "Worth a look"


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

    loc_pts, where = location_points(job.get("locations", []))
    score += loc_pts
    if loc_pts >= 25:
        reasons.append(f"{where} (top choice)")
    elif loc_pts > 0:
        reasons.append(f"{where} (good city)")
    elif loc_pts < 0:
        reasons.append("only in places you didn't pick")

    age_days = (time.time() - job.get("date_posted", 0)) / 86400
    if age_days <= 3:
        score += 20; reasons.append("posted in the last 3 days")
    elif age_days <= 7:
        score += 12; reasons.append("posted this week")
    else:
        score += 4

    if track_of(job, source) == "internship":
        score += SPRING_BONUS
        reasons.append("Spring 2027 internship (one of your goals)")
    if job.get("salary"):
        reasons.append(f"pays {job['salary']}")

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

    def recheck_pending(self):
        """When your rules change, jobs already waiting are re-checked against them."""
        dropped = 0
        for r in db.rows("SELECT id, company, title, locations, track FROM jobs WHERE status='pending'"):
            locs = [x.strip() for x in (r["locations"] or "").split(";") if x.strip()]
            bad = (any(b in r["company"].lower() for b in AVOID_COMPANIES)
                   or any(t in r["title"].lower() for t in SKIP_TITLE)
                   or not location_ok(locs)
                   or (r["track"] == "internship" and not commutable(locs)))
            if bad:
                db.execute("UPDATE jobs SET status='expired' WHERE id=?", (r["id"],))
                dropped += 1
        if dropped:
            self.log(f"Removed {dropped} waiting job{'s' if dropped != 1 else ''} that no longer fit your rules.")

    def expire_closed(self, feeds, failed_boards):
        """Postings that were taken down since yesterday leave your list (only for sources read today)."""
        active = {str(j.get("id")) for _, listing in feeds for j in listing if j.get("active", True)}
        read_lists = {src for src, _ in feeds if src != "company_boards"}
        gone = 0
        for r in db.rows("SELECT id, source FROM jobs WHERE status='pending'"):
            if r["id"] in active:
                continue
            board = re.match(r"gh-([a-z0-9]+)-", r["id"])
            if (board and board[1] not in failed_boards) or (not board and r["source"] in read_lists):
                db.execute("UPDATE jobs SET status='expired' WHERE id=?", (r["id"],))
                gone += 1
        if gone:
            self.log(f"Removed {gone} job{'s' if gone != 1 else ''} that were taken down.")

    def add_salaries(self, rows, limit=25):
        """Open the best postings that don't list pay yet and look for a salary on the page."""
        found, out = 0, []
        need = {r[0] for r in sorted(rows, key=lambda r: -r[8]) if not r[11]}
        need = set(list(need)[:limit])
        for r in rows:
            host = urllib.parse.urlparse(r[5]).hostname or "" if r[5] else ""
            if r[0] in need and host and self.may("network", host):  # only sites on the allowed list
                try:
                    pay = salary_from_page(self.fetch(r[5], timeout=15, raw=True))
                except Exception:
                    pay = ""   # site not on the list, blocked, or slow: just skip it
                if pay:
                    found += 1
                    r = r[:9] + (json.dumps(json.loads(r[9]) + [f"pays {pay}"]),) + r[10:11] + (pay,)
            out.append(r)
        if need:
            self.log(f"Found pay for {found} of the {len(need)} top postings that didn't list it.")
        return out

    def ai_rerank(self, rows):
        """One AI call for the whole batch (not one per job) to keep usage tiny."""
        top = []
        for r in sorted(rows, key=lambda r: -r[8]):
            signs = guard.check(f"{r[2]} {r[3]} {r[4]}")
            if signs:  # never show a suspicious posting to the AI
                db.add_finding("high", "Possible prompt injection", "job feed", f"{r[2]} – {guard.clean(r[3], 60)}",
                               "Posting text " + "; ".join(signs), "Kept away from the AI. Skip this posting.")
                self.log(f"Held back a suspicious posting from {r[2]}: {signs[0]}.", "alert")
                continue
            top.append(r)
            if len(top) == 25:
                break
        if not top:
            return rows
        listing = "\n".join(f"{i}. [{'Spring internship' if r[10] == 'internship' else 'full-time'}] "
                            f"{guard.clean(r[2], 60)} | {guard.clean(r[3], 120)} | {guard.clean(r[4], 60)}"
                            for i, r in enumerate(top))
        adj = self.think_json(
            f"{PROFILE}\n\nRate how well each job below fits him from -15 (bad fit) to +15 (great fit). "
            f"Reply with ONLY a JSON object mapping the number to [adjustment, 'short reason'].\n\n"
            + guard.wrap("job listings", listing), opener="{", max_tokens=1500)
        if not isinstance(adj, dict):
            self.log("AI second opinion unavailable today; kept rule scores.", "warn")
            return rows
        out = {r[0]: r for r in rows}
        for i, r in enumerate(top):
            a = adj.get(str(i))
            if isinstance(a, list) and len(a) == 2:
                delta = max(-15, min(15, int(a[0])))
                reasons = json.loads(r[9]) + [f"AI: {a[1]}"]
                out[r[0]] = r[:8] + (max(0, min(100, r[8] + delta)), json.dumps(reasons)) + r[10:]
        self.log(f"AI re-checked the top {len(top)} matches.")
        return list(out.values())

    def work(self):
        known = {r["id"] for r in db.rows("SELECT id FROM jobs")}
        liked, disliked = taste()
        applied = already_applied()
        new, checked = [], 0
        cutoff = time.time() - MAX_AGE_DAYS * 86400
        feeds = []
        for source, url in JOB_SOURCES.items():
            try:
                feeds.append((source, self.fetch(url)))
            except Exception as e:
                self.log(f"Couldn't read {source} list: {e}", "error")
        failed = []
        for slug in GREENHOUSE_COMPANIES:
            try:
                jobs = from_greenhouse(slug, self.fetch(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
                                                        timeout=20))
                feeds.append(("company_boards", jobs))
            except Exception:
                failed.append(slug)
        ok = len(GREENHOUSE_COMPANIES) - len(failed)
        if not feeds:  # nothing at all came in: that's a failure, not "no jobs today"
            raise RuntimeError("couldn't read any job source (internet down or sources moved)")
        self.log(f"Read {len(feeds) - ok} job lists and {ok}/{len(GREENHOUSE_COMPANIES)} company boards."
                 + (f" Couldn't reach: {', '.join(failed[:5])}{'…' if len(failed) > 5 else ''}." if failed else ""),
                 "warn" if failed else "info")
        self.expire_closed(feeds, failed)
        self.recheck_pending()
        seen_titles = set()
        for source, listings in feeds:
            for j in listings:
                if not j.get("active") or not j.get("is_visible", True) or j.get("date_posted", 0) < cutoff:
                    continue
                if not location_ok(j.get("locations", [])):
                    continue
                same = (j["company_name"].lower().strip(), j.get("title", "").lower().strip())
                if same in seen_titles:  # the same job listed in two places
                    continue
                seen_titles.add(same)
                src = source
                track = track_of(j, src)
                if track is None:
                    continue   # summer/fall internship: not one of your goals
                if track == "internship" and not commutable(j.get("locations", [])):
                    continue   # you're in class at GMU until May: DMV or fully remote only
                checked += 1
                jid = str(j.get("id"))
                if jid in known:
                    continue
                if any(a and (a in j["company_name"].lower() or j["company_name"].lower() in a)
                       for a in applied):
                    continue  # you've already applied there
                score, reasons = score_job(j, src, liked, disliked)
                if score < 50:
                    continue
                new.append((jid, time.time(), j["company_name"], j["title"],
                            "; ".join(j.get("locations", []))[:200], j.get("url", ""),
                            src, j.get("date_posted", 0), score, json.dumps(reasons), track,
                            j.get("salary", "")))
        new = self.ai_rerank(new)
        new = self.add_salaries(new)
        for row in new:
            db.execute("""INSERT OR IGNORE INTO jobs(id, ts, company, title, locations, url, source,
                          posted, score, reasons, track, salary) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", row)
        top = sorted(new, key=lambda r: -r[8])[:3]
        summary = ", ".join(f"{r[2]} ({r[8]})" for r in top) or "none"
        n_int = sum(1 for r in new if r[10] == "internship")
        self.log(f"Checked {checked} fresh listings, queued {len(new)} good matches "
                 f"({n_int} Spring internships, {len(new) - n_int} full-time jobs).")
        self.send("manager", f"Found {n_int} Spring internships and {len(new) - n_int} full-time jobs for you. "
                             f"Top: {summary}.")
        return {"checked": checked, "queued": len(new)}
