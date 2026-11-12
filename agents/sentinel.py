"""Sentinel: the city's security guard.

Scans your code folders the way a security team would, using proven tools when they're installed
and built-in checks when they're not. No AI needed, so it's free and runs in seconds.

  1. Leaked secrets    gitleaks if installed, else built-in patterns (API keys, tokens, private keys)
  2. Secret hygiene    .env files committed to git, .gitignore missing .env
  3. Vulnerable deps   pip-audit on every requirements.txt (known CVEs in your Python packages)
  4. Risky code        default passwords, TLS verification turned off, debug mode, shell=True
  5. Agent behavior    agents using far more tokens than usual, or crashing repeatedly

Secret values are never stored or shown: findings say WHERE a secret is and WHAT kind it is.
"""
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import db
from agents.base import Agent
from config import DAILY_TOKEN_BUDGET, ROOT, SENTINEL_TARGETS

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "env", "__pycache__", "data", "site-packages",
             ".mypy_cache", ".pytest_cache", "dist", "build"}
TEXT_EXT = {".py", ".js", ".ts", ".json", ".yml", ".yaml", ".toml", ".env", ".cfg", ".ini", ".txt",
            ".md", ".sh", ".html", ".mjs", ""}

SECRET_PATTERNS = {
    "Anthropic API key": r"sk-ant-[A-Za-z0-9_\-]{20,}",
    "OpenAI API key": r"sk-(?:proj-)?[A-Za-z0-9_\-]{32,}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}",
    "AWS access key": r"AKIA[0-9A-Z]{16}",
    "Slack token": r"xox[baprs]-[A-Za-z0-9\-]{10,}",
    "Telegram bot token": r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b",
    "Private key": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "Hard-coded secret": r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"][A-Za-z0-9_\-/+]{16,}['\"]",
}
PLACEHOLDER = re.compile(r"(?i)your|example|xxxx|changeme|placeholder|<|dummy|test")

RISKY_CODE = {
    "Default password left in code": (r"(?i)['\"](changeme|password123|admin123|default)['\"]",
                                      "Require a real secret from .env and refuse to start without it."),
    "TLS certificate checks turned off": (r"verify\s*=\s*False",
                                          "Remove verify=False so connections can't be intercepted."),
    "Debug mode on": (r"(?i)debug\s*=\s*True",
                      "Turn debug off outside your own machine; it can leak internals."),
    "Shell command built from text": (r"shell\s*=\s*True",
                                      "Pass a list of arguments instead of shell=True to avoid command injection."),
}


def fid(*parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def files_in(folder):
    for p in Path(folder).rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        if p.suffix.lower() in TEXT_EXT and p.stat().st_size < 1_000_000:
            yield p


def git_tracked(folder):
    try:
        out = subprocess.run(["git", "-C", str(folder), "ls-files"], capture_output=True, text=True, timeout=30)
        return set(out.stdout.split()) if out.returncode == 0 else None
    except Exception:
        return None


class Sentinel(Agent):
    name = "sentinel"
    tier = "worker"
    role = "Security guard: scans your code for leaked keys, vulnerable packages and risky code."

    def targets(self):
        found = [ROOT]
        for t in SENTINEL_TARGETS:
            p = Path(t).expanduser()
            if p.is_dir():
                # scan each project folder inside (e.g. ~/claude_code/trading-bot)
                subs = [s for s in p.iterdir() if s.is_dir() and not s.name.startswith(".")]
                found += subs or [p]
        return list(dict.fromkeys(found))

    def add(self, sev, check, target, location, detail, fix):
        key = fid(check, str(target), location, detail)
        db.execute("""INSERT INTO findings(id, ts, severity, check_name, target, location, detail, fix)
                      VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET ts=excluded.ts""",
                   (key, time.time(), sev, check, Path(target).name, location, detail, fix))
        return key

    # 1 + 2: secrets
    def scan_secrets(self, target):
        n = 0
        if shutil.which("gitleaks"):
            rep = Path("/tmp") / f"gitleaks-{fid(str(target))}.json"
            subprocess.run(["gitleaks", "detect", "--no-banner", "-s", str(target), "-f", "json",
                            "-r", str(rep), "--redact"], capture_output=True, timeout=300)
            if rep.exists():
                for f in json.loads(rep.read_text() or "[]"):
                    n += 1
                    self.add("high", "Leaked secret", target, f"{f.get('File')}:{f.get('StartLine')}",
                             f"{f.get('Description', 'secret')} (found by gitleaks, also checks git history)",
                             "Revoke/rotate the key now, then move it into .env.")
                rep.unlink()
        else:
            for p in files_in(target):
                try:
                    lines = p.read_text(errors="ignore").splitlines()
                except Exception:
                    continue
                for i, line in enumerate(lines, 1):
                    for kind, pat in SECRET_PATTERNS.items():
                        m = re.search(pat, line)
                        if m and not PLACEHOLDER.search(m.group(0)):  # first match per line only
                            is_env = p.name.startswith(".env")
                            n += 1
                            self.add("medium" if is_env else "high", "Leaked secret", target,
                                     f"{p.relative_to(target)}:{i}", kind,
                                     "Fine in a local .env that git ignores." if is_env else
                                     "Move it into .env, add .env to .gitignore, and rotate the key.")
                            break
        tracked = git_tracked(target)
        if tracked is not None:
            for f in tracked:
                if Path(f).name.startswith(".env") and not f.endswith((".example", ".sample")):
                    n += 1
                    self.add("high", "Secrets file committed", target, f,
                             ".env file is tracked by git, so anyone with the repo can read it",
                             "git rm --cached " + f + ", add it to .gitignore, rotate the keys inside.")
            gi = Path(target) / ".gitignore"
            if any(Path(target).glob(".env*")) and (not gi.exists() or ".env" not in gi.read_text()):
                n += 1
                self.add("medium", "Secret hygiene", target, ".gitignore",
                         ".env exists but .gitignore doesn't list it", "Add .env to .gitignore.")
        return n

    # 3: vulnerable packages
    def scan_deps(self, target):
        if not shutil.which("pip-audit"):
            return 0
        n = 0
        for req in Path(target).rglob("requirements*.txt"):
            if any(part in SKIP_DIRS for part in req.parts):
                continue
            out = subprocess.run(["pip-audit", "-r", str(req), "-f", "json", "--progress-spinner", "off"],
                                 capture_output=True, text=True, timeout=600)
            try:
                deps = json.loads(out.stdout).get("dependencies", [])
            except ValueError:
                self.log(f"pip-audit couldn't read {req.name} in {Path(target).name}", "warn")
                continue
            for d in deps:  # one finding per package, not per CVE, so the list stays readable
                vulns = d.get("vulns", [])
                if not vulns:
                    continue
                n += 1
                fixes = sorted({f for v in vulns for f in v.get("fix_versions", [])},
                               key=lambda x: [int(p) if p.isdigit() else 0 for p in x.split(".")])
                vulns = list({v["id"]: v for v in vulns}.values())  # pip-audit can repeat ids
                uniq = [v["id"] for v in vulns]
                ids = ", ".join(uniq[:3]) + (" …" if len(uniq) > 3 else "")
                self.add("high" if len(vulns) >= 5 else "medium", "Vulnerable package", target,
                         str(req.relative_to(target)),
                         f"{d['name']} {d.get('version', '')}: {len(vulns)} known vulnerabilities ({ids})",
                         f"Upgrade {d['name']} to {fixes[-1]} or newer." if fixes else "No fix released yet; watch it.")
        return n

    # 4: risky code
    def scan_code(self, target):
        n = 0
        for p in files_in(target):
            if p.suffix not in (".py", ".js", ".mjs", ".ts") or p.resolve() == Path(__file__).resolve():
                continue
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                for check, (pat, fix) in RISKY_CODE.items():
                    if re.search(pat, line) and not line.strip().startswith("#"):
                        n += 1
                        self.add("low", check, target, f"{p.relative_to(target)}:{i}", line.strip()[:80], fix)
        return n

    # 5: agents behaving oddly
    def scan_agents(self):
        n = 0
        since = time.time() - 86400
        for r in db.rows("SELECT agent, SUM(tokens) t FROM token_usage WHERE ts > ? GROUP BY agent", (since,)):
            if r["t"] and r["t"] > DAILY_TOKEN_BUDGET * 0.5:
                n += 1
                self.add("medium", "Agent overspending", "agent-city", r["agent"],
                         f"{r['agent']} used {r['t']:,} tokens today (over half the budget)",
                         "Check it isn't stuck in a loop; lower its limits.")
        for r in db.rows("SELECT agent, COUNT(*) c FROM activity WHERE kind='error' AND ts > ? GROUP BY agent", (since,)):
            if r["c"] >= 3:
                n += 1
                self.add("low", "Agent keeps failing", "agent-city", r["agent"],
                         f"{r['agent']} logged {r['c']} errors today", "Open its log and fix the cause.")
        return n

    def work(self):
        started = time.time()
        totals = {}
        for t in self.targets():
            name = Path(t).name
            self.log(f"Scanning {name}…")
            totals[name] = self.scan_secrets(t) + self.scan_deps(t) + self.scan_code(t)
        totals["agents"] = self.scan_agents()
        # anything not seen in this scan has been fixed
        db.execute("UPDATE findings SET status='fixed' WHERE ts < ? AND status='open'", (started,))
        open_ = db.rows("SELECT severity, COUNT(*) n FROM findings WHERE status='open' GROUP BY severity")
        counts = {r["severity"]: r["n"] for r in open_}
        tools = [t for t in ("gitleaks", "pip-audit") if shutil.which(t)]
        self.log(f"Scanned {len(totals) - 1} projects with {', '.join(tools) or 'built-in checks'}. "
                 f"Open issues: {counts.get('high', 0)} high, {counts.get('medium', 0)} medium, {counts.get('low', 0)} low.")
        if counts.get("high"):
            self.send("manager", f"ALERT: {counts['high']} high-risk security issue(s). See the Security panel.")
        else:
            self.send("manager", "Security scan clean: no high-risk issues.")
        return {"high": counts.get("high", 0), "medium": counts.get("medium", 0), "low": counts.get("low", 0)}
