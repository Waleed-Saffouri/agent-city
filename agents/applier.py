"""Applier: opens a job's application form on your Mac and fills it in for you.

Click "Fill application" on a drafted job and a browser window opens on the company's form with your
details, CV, cover letter and your standing answers (work authorization, sponsorship, the voluntary
demographic questions as you chose them, "how did you hear": company website, salary: 75% up the
posted range) already filled in. It NEVER presses Submit or ticks consent boxes: you check every
answer and submit yourself.

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
# Questions with a standing answer. Each entry: label pattern -> me.json key whose value is a list of
# option texts to try in order (the first one the form offers is picked).
CHOICE_FIELDS = [
    (r"authori[sz]ed to work|legally (authori[sz]ed|eligible)|eligible to work|right to work", "work_authorized"),
    (r"sponsor", "needs_sponsorship"),
    (r"18 years|over 18|at least 18", "over_18"),
    (r"hispanic|latin[oa]", "hispanic"),
    (r"\brace\b|ethnic", "race"),
    (r"veteran", "veteran"),
    (r"\bgender\b|\bsex\b", "gender"),
    (r"how did you (hear|find|learn)|where did you (hear|find|see)|source of (application|referral)", "heard_about"),
]
SKIP = r"pronoun|sexual orientation|transgender|disabilit|referred by|referral name|employee referral"
SALARY = r"salary|compensation|pay expectation|desired pay|expected pay"


def as_options(value):
    """Turn a me.json answer into the list of option texts to look for."""
    if value is True:
        return ["Yes"]
    if value is False:
        return ["No"]
    return value if isinstance(value, list) else [str(value)]


def salary_ask(job_salary):
    """Ask for the upper part of the posted range, without overreaching: 75% of the way from the low end
    to the high end, rounded. Returns '' when the posting gives no range (better blank than a guess)."""
    m = re.match(r"\$([\d.]+)(K?)–\$([\d.]+)(K?)(/hr)?", job_salary or "")
    if not m:
        return ""
    lo, hi = float(m[1]), float(m[3])
    if m[5]:   # hourly
        return f"{round(lo + 0.75 * (hi - lo))}"
    lo, hi = lo * 1000, hi * 1000
    return f"{int(round((lo + 0.75 * (hi - lo)) / 1000) * 1000)}"


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
        // for radio buttons, the question is on the group, the answer is on the button
        let group = '';
        if (el.type === 'radio' || el.type === 'checkbox') {
          const fs = el.closest('fieldset'); const rg = el.closest('[role=radiogroup], [role=group]');
          group = (fs && (fs.querySelector('legend') || {}).innerText) || (rg && (rg.getAttribute('aria-label') ||
                  (document.getElementById(rg.getAttribute('aria-labelledby') || '') || {}).innerText)) || '';
          if (!group) { let p = el.parentElement; for (let k = 0; k < 4 && p; k++, p = p.parentElement) {
            const q = p.querySelector('label:not(:has(input)), legend, p, span.question'); if (q && !q.contains(el)) { group = q.innerText; break; } } }
        }
        const combo = el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list' ||
                      /select__input|react-select/.test(el.className + ' ' + (el.id || ''));
        el.setAttribute('data-ac-idx', i);
        out.push({idx: i, tag: el.tagName.toLowerCase(), type: el.type || '', text: text.trim().slice(0, 200),
                  group: (group || '').trim().slice(0, 200), combo, checked: !!el.checked,
                  value: el.value || '', accept: el.accept || ''});
      });
      return out;
    }""")


def matches(option_text, wanted):
    """Short answers (Yes/No/Male) must start the option; longer ones may appear anywhere in it."""
    o, w = option_text.strip().lower(), wanted.strip().lower()
    return o.startswith(w) if len(w) <= 4 else w in o


def choose(page, sel, wanted_list):
    """Native dropdown: pick the first wanted answer the form offers. Returns the option text or None."""
    options = page.evaluate("(sel) => [...document.querySelector(sel).options].map(o => o.text)", sel)
    for w in wanted_list:
        for i, text in enumerate(options):
            if text.strip() and matches(text, w):
                page.evaluate("""([sel, i]) => { const el = document.querySelector(sel); el.selectedIndex = i;
                    el.dispatchEvent(new Event('change', {bubbles: true})); }""", [sel, i])
                return text.strip()
    return None


def choose_combo(page, sel, wanted_list):
    """Searchable dropdown (React Select on Greenhouse, etc.): open it, type, take the matching option."""
    for w in wanted_list:
        try:
            page.click(sel, timeout=2000)
            page.fill(sel, w)
            page.wait_for_timeout(500)
            opts = page.locator("[role=option], .select__option, [id*='-option-']")
            for k in range(min(opts.count(), 30)):
                t = opts.nth(k).inner_text()
                if matches(t, w):
                    opts.nth(k).click()
                    return t.strip()
            page.keyboard.press("Escape")
        except Exception:
            continue
    return None


def fill_form(page, me, cv_path, letter_path, letter_text, job_salary=""):
    """Fill what we can. Returns (filled labels, skipped labels)."""
    filled, skipped = [], []
    radio_done = set()
    for f in label_texts(page):
        label = re.sub(r"\s+", " ", f["text"]).lower().replace("*", "").strip()
        question = re.sub(r"\s+", " ", f["group"] or f["text"]).lower().replace("*", "").strip()
        sel = f'[data-ac-idx="{f["idx"]}"]'
        short = (f["group"] or f["text"]).split("\n")[0][:60]
        if not question or re.search(SKIP, question):
            if question:
                skipped.append(short)
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
            if f["type"] == "radio":   # pick the button whose own label is the answer
                for pat, key in CHOICE_FIELDS:
                    if re.search(pat, question) and key in me and question not in radio_done:
                        if any(matches(f["text"], w) for w in as_options(me[key])):
                            page.check(sel); radio_done.add(question); filled.append(f"{short} → {f['text'][:30]}")
                        break
                continue
            if f["type"] == "checkbox" or (f["value"] and f["tag"] != "select" and not f["combo"]):
                continue   # never overwrite what's there; checkboxes (consents) are yours
            if re.search(SALARY, question):
                ask = salary_ask(job_salary)
                if ask and f["tag"] != "select" and not f["combo"]:
                    page.fill(sel, ask); filled.append(f"{short} → {ask}")
                else:
                    skipped.append(short + " (no posted range to base it on)")
                continue
            for pat, key in CHOICE_FIELDS:
                if re.search(pat, question) and key in me:
                    opts = as_options(me[key])
                    if f["tag"] == "select":
                        got = choose(page, sel, opts)
                    elif f["combo"]:
                        got = choose_combo(page, sel, opts)
                    else:   # free-text version of the question
                        page.fill(sel, opts[0]); got = opts[0]
                    (filled.append(f"{short} → {got}") if got else skipped.append(short))
                    break
            else:
                for pat, key in TEXT_FIELDS:
                    if re.search(pat, label) and me.get(key):
                        if f["tag"] == "select":
                            if not choose(page, sel, [str(me[key])]):
                                skipped.append(short); break
                        elif f["combo"]:
                            if not choose_combo(page, sel, [str(me[key])]):
                                skipped.append(short); break
                        elif f["type"] in ("text", "email", "tel", "url", "search", "") or f["tag"] == "textarea":
                            page.fill(sel, str(me[key]))
                        filled.append(short)
                        break
        except Exception:
            skipped.append(short)
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
            filled, skipped = fill_form(page, me, cv, letter_pdf, letter_text, job.get("salary", ""))
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
                        more, _ = fill_form(current, me, cv, letter_pdf, letter_text, job.get("salary", ""))
                        current.evaluate(BANNER_JS)
                        self.log(f"Filled {len(more)} more fields.")
                    except Exception as e:
                        self.log(f"Couldn't fill that page: {e}", "warn")
                time.sleep(1)
            db.put("applier_open", "")
        return {"ok": True, "filled": filled, "skipped": skipped}

    def work(self):
        return {"ready": bool(load_me())}
