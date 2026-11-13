"""Applier: opens a job's application form on your Mac and fills it in for you.

Click "Fill application" on a drafted job and a browser window opens on the company's form with your
details, CV and cover letter already filled in. It NEVER presses Submit: you check every answer,
fill anything it left blank (demographic questions, anything unusual), and submit yourself.

Works best on Greenhouse, Lever and Ashby forms (most company boards). Workday and similar sites need
you to sign in first; once you're on the form, press "Fill again" in Agent City.

Your details come from profile/me.json (private, never committed).
"""
import json
import re
import time
from pathlib import Path

import db
from agents.base import Agent
from config import ROOT

ME_PATH = ROOT / "profile" / "me.json"
DEFAULT_CV = ROOT / "profile" / "cv.pdf"

# label pattern -> key in me.json (first match wins, so specific patterns come first)
TEXT_FIELDS = [
    (r"first name|given name|preferred first", "first_name"),
    (r"last name|family name|surname", "last_name"),
    (r"^(full )?name\b|legal name|your name", "full_name"),
    (r"e-?mail", "email"),
    (r"phone|mobile", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"website|portfolio|personal (site|url)", "github"),
    (r"street|address line 1|^address", "street"),
    (r"\bcity\b", "city"),
    (r"\bstate\b|province", "state"),
    (r"zip|postal", "zip"),
    (r"current (location|city)|^location", "location"),
    (r"school|university|college|institution", "school"),
    (r"degree", "degree"),
    (r"discipline|major|field of study", "major"),
    (r"\bgpa\b|grade point", "gpa"),
    (r"graduation (date|year)|expected graduation|grad(uation)? year", "graduation"),
]
# yes/no questions with a truthful standing answer
CHOICE_FIELDS = [
    (r"authori[sz]ed to work|legally (authori[sz]ed|eligible)|eligible to work", "work_authorized"),
    (r"sponsor", "needs_sponsorship"),
    (r"18 years|over 18|at least 18", "over_18"),
]
SKIP = r"gender|race|ethnic|veteran|disabilit|hispanic|pronoun|sexual|salary|compensation|how did you hear|referr"

BANNER_JS = """
(() => { const b = document.createElement('div');
  b.textContent = 'Agent City filled what it could. Check every answer, fill the blanks, then press Submit yourself.';
  Object.assign(b.style, {position:'fixed', top:'0', left:'0', right:'0', zIndex:2147483647, background:'#c9962b',
    color:'#fff', font:'600 15px system-ui', padding:'10px 16px', textAlign:'center'});
  document.body.appendChild(b); })();
"""


def load_me():
    try:
        me = json.loads(ME_PATH.read_text())
    except (OSError, ValueError):
        return None
    me.setdefault("full_name", f"{me.get('first_name', '')} {me.get('last_name', '')}".strip())
    return me


def label_texts(page):
    """Every form field with the text a person would read next to it."""
    return page.evaluate("""() => {
      const out = [];
      const fields = document.querySelectorAll('input, textarea, select');
      fields.forEach((el, i) => {
        if (el.type === 'hidden' || el.disabled) return;
        let text = '';
        if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) text = l.innerText; }
        if (!text) { const l = el.closest('label'); if (l) text = l.innerText; }
        if (!text) text = el.getAttribute('aria-label') || '';
        if (!text) { const box = el.closest('div, fieldset, li'); if (box) text = (box.querySelector('label, legend') || {}).innerText || ''; }
        if (!text) text = el.placeholder || el.name || '';
        el.setAttribute('data-ac-idx', i);
        out.push({idx: i, tag: el.tagName.toLowerCase(), type: el.type || '', text: text.trim().slice(0, 200),
                  value: el.value || '', accept: el.accept || ''});
      });
      return out;
    }""")


def choose(page, sel, wanted):
    """Pick the dropdown option whose text starts with `wanted` (e.g. "Yes"). True if found."""
    return page.evaluate("""([sel, wanted]) => {
      const el = document.querySelector(sel); if (!el) return false;
      const opt = [...el.options].find(o => o.text.trim().toLowerCase().startsWith(wanted.toLowerCase()));
      if (!opt) return false;
      el.value = opt.value; el.dispatchEvent(new Event('change', {bubbles: true})); return true;
    }""", [sel, wanted])


def fill_form(page, me, cv_path, letter_path, letter_text):
    """Fill what we can. Returns (filled labels, skipped labels)."""
    filled, skipped = [], []
    for f in label_texts(page):
        label = re.sub(r"\s+", " ", f["text"]).lower().replace("*", "").strip()
        sel = f'[data-ac-idx="{f["idx"]}"]'
        if not label or re.search(SKIP, label):
            if label:
                skipped.append(f["text"])
            continue
        try:
            if f["type"] == "file":
                if re.search(r"cover", label) and letter_path:
                    page.set_input_files(sel, str(letter_path)); filled.append("Cover letter (file)")
                elif re.search(r"resume|résumé|cv\b|curriculum", label) and cv_path:
                    page.set_input_files(sel, str(cv_path)); filled.append("CV")
                continue
            if f["tag"] == "textarea" and re.search(r"cover letter", label) and letter_text:
                page.fill(sel, letter_text); filled.append("Cover letter (text)")
                continue
            if f["value"]:
                continue   # never overwrite something already there
            for pat, key in CHOICE_FIELDS:
                if re.search(pat, label) and key in me:
                    answer = "Yes" if me[key] else "No"
                    if f["tag"] == "select" and choose(page, sel, answer):
                        filled.append(f"{f['text'][:50]} → {answer}")
                    else:
                        skipped.append(f["text"])
                    break
            else:
                for pat, key in TEXT_FIELDS:
                    if re.search(pat, label) and me.get(key):
                        if f["tag"] == "select":
                            if not choose(page, sel, str(me[key])):
                                skipped.append(f["text"]); break
                        elif f["type"] in ("text", "email", "tel", "url", "search", "") or f["tag"] == "textarea":
                            page.fill(sel, str(me[key]))
                        filled.append(f["text"].split("\n")[0][:60])
                        break
        except Exception:
            skipped.append(f["text"])
    return filled, skipped


class Applier(Agent):
    name = "applier"
    tier = "worker"
    role = "Opens a job's application form and fills in your details, CV and cover letter. You submit."

    def make_letter_pdf(self, folder):
        html = folder / "cover_letter.html"
        pdf = folder / "Waleed_Saffouri_Cover_Letter.pdf"
        if not html.exists():
            return None
        if pdf.exists() and pdf.stat().st_mtime >= html.stat().st_mtime:
            return pdf
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page()
            page.goto(html.as_uri())
            page.pdf(path=str(pdf), format="Letter", print_background=True)
            b.close()
        return pdf

    def fill(self, job_id):
        """Open the form and fill it, then keep the window open (and listen for "Fill again")
        until you close it. Runs in its own thread, because the browser stays open."""
        me = load_me()
        if not me:
            self.log("Can't fill forms yet: profile/me.json is missing (your contact details).", "error")
            return {"ok": False}
        job = db.rows("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            return {"ok": False}
        job = job[0]
        draft = db.rows("SELECT folder FROM drafts WHERE job_id=?", (job_id,))
        folder = Path(draft[0]["folder"]) if draft else None
        cv = (folder / "cv.pdf") if folder and (folder / "cv.pdf").exists() else (DEFAULT_CV if DEFAULT_CV.exists() else None)
        letter_pdf = self.make_letter_pdf(folder) if folder else None
        letter_text = (folder / "cover_letter.md").read_text() if folder and (folder / "cover_letter.md").exists() else ""

        db.set_state(self.name, "working")
        self.log(f"Opening the {job['company']} application…")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=False, channel="chrome")   # your installed Chrome
            except Exception:
                browser = p.chromium.launch(headless=False)                     # or Playwright's Chromium
            page = browser.new_page()
            page.goto(job["url"], wait_until="domcontentloaded", timeout=60000)
            time.sleep(2)
            for label in ("Apply for this job", "Apply now", "Apply", "I'm interested"):   # get past the description
                btn = page.get_by_role("button", name=re.compile(label, re.I))
                if not btn.count():
                    btn = page.get_by_role("link", name=re.compile(rf"^\s*{label}\s*$", re.I))
                if btn.count():
                    try:
                        btn.first.click(timeout=3000); time.sleep(2); break
                    except Exception:
                        pass
            filled, skipped = fill_form(page, me, cv, letter_pdf, letter_text)
            page.evaluate(BANNER_JS)
            self.log(f"Filled {len(filled)} fields on the {job['company']} form"
                     + (f"; left {len(skipped)} for you" if skipped else "") + ". Check it and press Submit yourself.")
            if len(filled) < 3:
                self.log("This site wants you to sign in or click through first. Do that, then press Fill again.", "warn")
            db.set_state(self.name, "idle", {"filled": len(filled), "company": job["company"]})
            db.put("applier_open", job_id)
            # keep the window; "Fill again" (after you sign in) fills whatever page is showing now
            while browser.is_connected():
                if db.get("applier_again") == "1":
                    db.put("applier_again", "0")
                    try:
                        current = browser.contexts[0].pages[-1]
                        more, _ = fill_form(current, me, cv, letter_pdf, letter_text)
                        current.evaluate(BANNER_JS)
                        self.log(f"Filled {len(more)} more fields.")
                    except Exception as e:
                        self.log(f"Couldn't fill that page: {e}", "warn")
                time.sleep(1)
            db.put("applier_open", "")
        return {"ok": True, "filled": filled, "skipped": skipped}

    def work(self):
        return {"ready": bool(load_me())}
