"""Writer: when you approve a job, drafts a tailored cover letter and CV notes for it.

Rules it follows (the same ones a careful human would):
  * Your CV file is the ONLY source of facts. It never invents experience, numbers or skills.
  * Every number in the draft is checked against your CV. If one isn't there, the draft is
    rewritten once; if it still fails, it's marked "needs your review" instead of "ready".
  * The job posting is untrusted text, so it goes through the prompt-injection guard.
"""
import html
import json
import re
import time
from pathlib import Path

import db
from agents import guard
from agents.base import Agent
from config import CV_PATH, DRAFTS_DIR, PROFILE_NAME

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


def numbers(text):
    return {n.rstrip(".,").replace(",", "") for n in NUM.findall(text or "")}


def unsupported_numbers(draft, *sources):
    allowed = set().union(*(numbers(s) for s in sources)) | {"2026", "2027", "1", "2", "3"}
    return sorted(n for n in numbers(draft) - allowed if len(n) > 1 or n not in "0123456789")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "job"


LETTER_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Cover letter: {company}</title>
<style>body{{font:11.5pt/1.55 Georgia,serif;color:#111827;max-width:6.5in;margin:.9in auto}}
h1{{font:600 18pt/1.2 Georgia,serif;color:#1f3a5f;margin:0}} .who{{color:#4b5563;font-size:10pt;margin:4px 0 28px}}
p{{margin:0 0 12px}} @media print{{body{{margin:.6in auto}}}}</style></head>
<body><h1>{name}</h1><div class="who">{contact}</div>{body}</body></html>"""


class Writer(Agent):
    name = "writer"
    tier = "manager"   # writing quality matters, so it uses the mid-tier model
    role = "Drafts a tailored cover letter and CV notes for every job you approve."

    def pending(self):
        return db.rows("""SELECT j.* FROM jobs j LEFT JOIN drafts d ON d.job_id = j.id
                          WHERE j.status = 'approved' AND d.job_id IS NULL ORDER BY j.ts""")

    def job_text(self, job):
        """Full posting text when the company board offers it (Greenhouse); else title only."""
        m = re.match(r"gh-([a-z0-9]+)-(\d+)$", job["id"])
        if not m:
            return ""
        try:
            data = self.fetch(f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}", timeout=20)
            text = re.sub(r"<[^>]+>", " ", html.unescape(data.get("content", "")))
            return re.sub(r"\s+", " ", text)[:6000]
        except Exception:
            return ""

    def draft(self, job, cv, posting):
        flags = guard.check(posting)
        if flags:
            self.log(f"{job['company']} posting looked suspicious ({flags[0]}); writing from the title only.", "alert")
            posting = ""
        ask = (
            f"Write a cover letter for {PROFILE_NAME} applying to '{job['title']}' at {job['company']} "
            f"({job['locations']}).\n\nHARD RULES:\n"
            "- Use ONLY facts found in the CV below. Do not invent employers, numbers, tools or results.\n"
            "- 3 short paragraphs, under 260 words, plain and specific, no clichés like 'I am excited to'.\n"
            "- Connect 2 concrete CV items to what this role needs.\n"
            "- Then a line '---' and 3 bullets: which CV projects/bullets to put first for this job.\n\n"
            f"CV:\n{cv}\n\n" + (guard.wrap("job posting", posting) if posting else "No posting text available; use the title."))
        text = self.think(ask, max_tokens=1200)
        if not text:
            return None, "no AI available"
        bad = unsupported_numbers(text, cv, posting, job["title"])
        if bad:
            text = self.think(ask + f"\n\nYour last draft used numbers not in the CV ({', '.join(bad)}). "
                              "Rewrite without them.", max_tokens=1200) or text
            bad = unsupported_numbers(text, cv, posting, job["title"])
        return text, (f"check these numbers: {', '.join(bad)}" if bad else "")

    def save(self, job, text, problem):
        folder = DRAFTS_DIR / f"{slug(job['company'])}-{slug(job['title'])}"
        folder.mkdir(parents=True, exist_ok=True)
        letter, _, notes = text.partition("---")
        contact = " | ".join(x for x in [l.strip() for l in CV_PATH.read_text().splitlines()[2:3]] if x)
        paras = "".join("<p>" + html.escape(p.strip()).replace("\n", "<br>") + "</p>"
                        for p in letter.strip().split("\n\n") if p.strip())
        (folder / "cover_letter.html").write_text(LETTER_HTML.format(
            company=html.escape(job["company"]), name=html.escape(PROFILE_NAME), contact=html.escape(contact), body=paras))
        (folder / "cover_letter.md").write_text(letter.strip() + "\n")
        (folder / "cv_notes.md").write_text(f"# What to lead with for {job['company']}\n\n{notes.strip()}\n\n"
                                            f"Job link: {job['url']}\n")
        status = "review" if problem else "ready"
        db.execute("INSERT OR REPLACE INTO drafts(job_id, ts, folder, status, note) VALUES (?,?,?,?,?)",
                   (job["id"], time.time(), str(folder), status, problem))
        return status

    def work(self):
        if not CV_PATH.exists():
            self.log(f"No CV found at {CV_PATH}. Put your CV there (see profile/cv.example.md).", "error")
            return {"drafted": 0}
        cv = CV_PATH.read_text()
        done = 0
        for job in self.pending():
            text, problem = self.draft(job, cv, self.job_text(job))
            if not text:
                self.log(f"Couldn't draft {job['company']}: {problem}.", "warn")
                continue
            status = self.save(job, text, problem)
            done += 1
            self.log(f"Drafted cover letter for {job['company']} ({'ready' if status == 'ready' else problem}).")
            self.send("manager", f"Cover letter for {job['company']} – {job['title']} is {status}.")
        return {"drafted": done}
