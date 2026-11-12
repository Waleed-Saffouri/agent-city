"""Sentinel: the city's security guard.

Works the way a small security team would: proven open-source scanners first, built-in checks
as a fallback, and prevention (a pre-commit hook) instead of only detection. No AI, so it's free.

  1. Leaked secrets     gitleaks over the files AND the full git history (a key you deleted
                        is still in history); built-in patterns if gitleaks isn't installed
  2. Secret hygiene     .env files committed to git, .gitignore missing .env
  3. Vulnerable deps    pip-audit: known CVEs in your Python packages, one finding per package
  4. Risky code         Bandit (understands Python, not just text); regex fallback
  5. Prevention         is the pre-commit hook installed? `python run.py protect <repo>` adds it
  6. Agent behavior     blocked permission attempts, prompt-injection attempts, runaway token use

Secret values are never stored or shown: findings say WHERE a secret is and WHAT kind it is.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so the git hook can run this file

import db
from agents.base import Agent
from config import DAILY_TOKEN_BUDGET, ROOT, SENTINEL_TARGETS

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "env", "__pycache__", "data", "site-packages",
             ".mypy_cache", ".pytest_cache", "dist", "build", "tests"}
TEXT_EXT = {".py", ".js", ".ts", ".json", ".yml", ".yaml", ".toml", ".env", ".cfg", ".ini", ".txt",
            ".md", ".sh", ".html", ".mjs", ""}

SECRET_PATTERNS = {
    "Anthropic API key": r"sk-ant-[A-Za-z0-9_\-]{20,}",
    "OpenAI API key": r"sk-(?!ant-)(?:proj-)?[A-Za-z0-9_\-]{32,}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}",
    "AWS access key": r"AKIA[0-9A-Z]{16}",
    "Slack token": r"xox[baprs]-[A-Za-z0-9\-]{10,}",
    "Telegram bot token": r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b",
    "Alpaca API key": r"\b(?:PK|AK)[A-Z0-9]{18}\b",
    "Private key": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "Hard-coded secret": r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"][A-Za-z0-9_\-/+]{16,}['\"]",
}
PLACEHOLDER = re.compile(r"(?i)your|example|xxxx|changeme|placeholder|<|dummy|test|fake")

RISKY_CODE = {  # fallback only, when Bandit isn't installed
    "Default password left in code": (r"(?i)['\"](changeme|password123|admin123)['\"]",
                                      "Require a real secret from .env and refuse to start without it."),
    "TLS certificate checks turned off": (r"verify\s*=\s*False",
                                          "Remove verify=False so connections can't be intercepted."),
    "Shell command built from text": (r"shell\s*=\s*True",
                                      "Pass a list of arguments instead of shell=True to avoid command injection."),
}

HOOK_MARK = "# agent-city sentinel hook"


def tool(name):
    """Find a scanner on PATH or next to this Python (inside .venv/bin when started by start.sh)."""
    found = shutil.which(name)
    if found:
        return found
    local = Path(sys.executable).parent / name
    return str(local) if local.exists() else None


def files_in(folder):
    for p in Path(folder).rglob("*"):
        if any(part in SKIP_DIRS for part in p.relative_to(folder).parts) or not p.is_file():
            continue
        if p.suffix.lower() in TEXT_EXT and p.stat().st_size < 1_000_000:
            yield p


def git(folder, *args):
    try:
        out = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, timeout=120)
        return out.stdout if out.returncode == 0 else None
    except Exception:
        return None


def find_secrets_in_text(text):
    """Yield (line_no, kind) for every likely secret. First match per line only."""
    for i, line in enumerate(text.splitlines(), 1):
        for kind, pat in SECRET_PATTERNS.items():
            m = re.search(pat, line)
            if m and not PLACEHOLDER.search(m.group(0)):
                yield i, kind
                break


def install_hook(repo):
    """Add a git pre-commit hook that refuses commits containing secrets. Returns a status string."""
    hooks = Path(repo) / ".git" / "hooks"
    if not hooks.is_dir():
        return "not a git repo"
    hook = hooks / "pre-commit"
    if hook.exists() and HOOK_MARK in hook.read_text():
        return "already installed"
    if hook.exists():
        return "a different pre-commit hook exists; add Sentinel to it by hand"
    hook.write_text(f"""#!/bin/sh
{HOOK_MARK}
# Blocks the commit if the staged changes contain a secret.
if command -v gitleaks >/dev/null 2>&1; then
  gitleaks git --pre-commit --staged --redact --no-banner || {{
    echo "Sentinel: commit blocked - a secret was found in your staged changes."; exit 1; }}
else
  PY="{sys.executable}"; [ -x "$PY" ] || PY=python3
  "$PY" "{ROOT / 'agents' / 'sentinel.py'}" --staged || exit 1
fi
""")
    hook.chmod(0o755)
    return "installed"


def check_staged():
    """Used by the pre-commit hook when gitleaks isn't installed."""
    diff = git(".", "diff", "--cached", "-U0", "--no-color") or ""
    hits, current = [], "?"
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            for _, kind in find_secrets_in_text(line[1:]):
                hits.append(f"{current}: {kind}")
    for h in hits:
        print(f"Sentinel: possible secret in {h}")
    if hits:
        print("Commit blocked. Move secrets into .env (which git ignores) and try again.")
    return 1 if hits else 0


class Sentinel(Agent):
    name = "sentinel"
    tier = "worker"
    role = "Security guard: blocks leaked keys before they're committed and scans your projects daily."

    def targets(self):
        found = [ROOT]
        for t in SENTINEL_TARGETS:
            p = Path(t).expanduser()
            if p.is_dir():
                subs = [s for s in p.iterdir() if s.is_dir() and not s.name.startswith(".")]
                found += subs or [p]
        return list(dict.fromkeys(Path(f).resolve() for f in found))

    def add(self, sev, check, target, location, detail, fix):
        return db.add_finding(sev, check, Path(target).name, location, detail, fix)

    # 1. secrets (files + git history)
    def scan_secrets(self, target):
        n = 0
        is_repo = (Path(target) / ".git").exists()
        if tool("gitleaks"):
            with tempfile.TemporaryDirectory() as tmp:
                for mode in (["git"] if is_repo else []) + ["dir"]:
                    rep = Path(tmp) / f"{mode}.json"
                    subprocess.run([tool("gitleaks"), mode, str(target), "--no-banner", "--redact",
                                    "-f", "json", "-r", str(rep)], capture_output=True, timeout=600)
                    if not rep.exists():
                        continue
                    for f in json.loads(rep.read_text() or "[]"):
                        where = f"{f.get('File')}:{f.get('StartLine')}"
                        in_history = mode == "git"
                        n += 1
                        self.add("high", "Leaked secret", target, where,
                                 f"{f.get('Description') or f.get('RuleID')}"
                                 + (f" (in git history, commit {f.get('Commit', '')[:7]})" if in_history else ""),
                                 "Rotate the key now (deleting it isn't enough once committed), then keep it in .env.")
        # Built-in patterns always run too: they cover keys gitleaks's rules can miss
        # (e.g. Alpaca and Telegram tokens). Duplicates collapse because findings are keyed by location.
        ignored = set()
        if is_repo:
            envs = [str(p.relative_to(target)) for p in Path(target).glob(".env*")]
            out = subprocess.run(["git", "-C", str(target), "check-ignore", *envs],
                                 capture_output=True, text=True) if envs else None
            ignored = set(out.stdout.split()) if out else set()
        for p in files_in(target):
            if str(p.relative_to(target)) in ignored:
                continue  # secrets in a git-ignored .env are exactly where they should be
            try:
                hits = list(find_secrets_in_text(p.read_text(errors="ignore")))
            except Exception:
                continue
            for i, kind in hits:
                is_env = p.name.startswith(".env")
                n += 1
                self.add("medium" if is_env else "high", "Leaked secret", target,
                         f"{p.relative_to(target)}:{i}", kind,
                         "Fine in a local .env that git ignores." if is_env else
                         "Move it into .env, add .env to .gitignore, and rotate the key.")
        if is_repo:  # built-in history scan: lines ever added in any commit
            hist = git(target, "log", "--all", "-p", "--no-color", "-U0", "--format=commit %h") or ""
            commit, current = "?", "?"
            for line in hist.splitlines():
                if line.startswith("commit "):
                    commit = line[7:]
                elif line.startswith("+++ b/"):
                    current = line[6:]
                elif line.startswith("+") and not line.startswith("+++"):
                    for _, kind in find_secrets_in_text(line[1:]):
                        n += 1
                        self.add("high", "Leaked secret", target, current,
                                 f"{kind} (in git history, commit {commit})",
                                 "Rotate the key; it stays in history even after you delete it.")
        return n

    # 2. hygiene
    def scan_hygiene(self, target):
        n = 0
        tracked = git(target, "ls-files")
        if tracked is None:
            return 0
        for f in tracked.split():
            if Path(f).name.startswith(".env") and not f.endswith((".example", ".sample")):
                n += 1
                self.add("high", "Secrets file committed", target, f,
                         ".env file is tracked by git, so anyone with the repo can read it",
                         f"git rm --cached {f}, add it to .gitignore, rotate the keys inside.")
        gi = Path(target) / ".gitignore"
        if any(Path(target).glob(".env*")) and (not gi.exists() or ".env" not in gi.read_text()):
            n += 1
            self.add("medium", "Secret hygiene", target, ".gitignore",
                     ".env exists but .gitignore doesn't list it", "Add .env to .gitignore.")
        return n

    # 3. vulnerable packages
    def scan_deps(self, target):
        if not tool("pip-audit"):
            return 0
        n = 0
        for req in Path(target).rglob("requirements*.txt"):
            if any(part in SKIP_DIRS for part in req.relative_to(target).parts):
                continue
            out = subprocess.run([tool("pip-audit"), "-r", str(req), "-f", "json", "--progress-spinner", "off"],
                                 capture_output=True, text=True, timeout=600)
            try:
                deps = json.loads(out.stdout).get("dependencies", [])
            except ValueError:
                self.log(f"pip-audit couldn't read {req.name} in {Path(target).name}", "warn")
                continue
            for d in deps:  # one finding per package, not per CVE
                vulns = list({v["id"]: v for v in d.get("vulns", [])}.values())
                if not vulns:
                    continue
                n += 1
                fixes = sorted({f for v in vulns for f in v.get("fix_versions", [])},
                               key=lambda x: [int(p) if p.isdigit() else 0 for p in x.split(".")])
                ids = ", ".join(v["id"] for v in vulns[:3]) + (" …" if len(vulns) > 3 else "")
                self.add("high" if len(vulns) >= 5 else "medium", "Vulnerable package", target,
                         str(req.relative_to(target)),
                         f"{d['name']} {d.get('version', '')}: {len(vulns)} known vulnerabilities ({ids})",
                         f"Upgrade {d['name']} to {fixes[-1]} or newer." if fixes else "No fix released yet; watch it.")
        return n

    # 4. risky code
    def scan_code(self, target):
        n = 0
        if tool("bandit"):
            out = subprocess.run([tool("bandit"), "-r", str(target), "-f", "json", "-q",
                                  "-x", ",".join(f"*/{d}/*" for d in SKIP_DIRS)],
                                 capture_output=True, text=True, timeout=600)
            try:
                results = json.loads(out.stdout or "{}").get("results", [])
            except ValueError:
                results = []
            for r in results:
                if r["issue_confidence"] == "LOW" or r["issue_severity"] == "LOW":
                    continue  # skip noise; keep what Bandit is confident matters
                sev = "high" if r["issue_severity"] == "HIGH" and r["issue_confidence"] == "HIGH" else "low"
                n += 1
                rel = Path(r["filename"]).resolve().relative_to(target)
                self.add(sev, f"Risky code ({r['test_id']})", target, f"{rel}:{r['line_number']}",
                         r["issue_text"], f"See {r.get('more_info', 'Bandit docs')}")
            return n
        for p in files_in(target):
            if p.suffix != ".py" or p.resolve() == Path(__file__).resolve():
                continue
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                for check, (pat, fix) in RISKY_CODE.items():
                    if re.search(pat, line) and not line.strip().startswith("#"):
                        n += 1
                        self.add("low", check, target, f"{p.relative_to(target)}:{i}", line.strip()[:80], fix)
        return n

    # 5. prevention
    def scan_prevention(self, target):
        hook = Path(target) / ".git" / "hooks" / "pre-commit"
        if (Path(target) / ".git").is_dir() and not (hook.exists() and HOOK_MARK in hook.read_text()):
            self.add("low", "No leak protection", target, ".git/hooks",
                     "Nothing stops a secret from being committed here",
                     f"Run: python run.py protect {target}")
            return 1
        return 0

    # 6. the agents themselves
    def scan_agents(self):
        n = 0
        since = time.time() - 86400
        for r in db.rows("SELECT agent, text FROM activity WHERE kind='blocked' AND ts > ?", (since,)):
            n += 1
            self.add("high", "Agent broke its permissions", "agent-city", r["agent"], r["text"],
                     "Find out why it tried this; if it's legitimate, add it to PERMISSIONS in config.py.")
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
        scanned = 0
        for t in self.targets():
            self.log(f"Scanning {Path(t).name}…")
            self.scan_secrets(t); self.scan_hygiene(t); self.scan_deps(t)
            self.scan_code(t); self.scan_prevention(t)
            scanned += 1
        self.scan_agents()
        # Findings from scanners that weren't seen again this run are fixed.
        # (Prompt-injection findings come from the Scout, so they're left for you to dismiss.)
        db.execute("""UPDATE findings SET status='fixed'
                      WHERE ts < ? AND status='open' AND check_name != 'Possible prompt injection'""", (started,))
        counts = {r["severity"]: r["n"] for r in
                  db.rows("SELECT severity, COUNT(*) n FROM findings WHERE status='open' GROUP BY severity")}
        tools = [t for t in ("gitleaks", "pip-audit", "bandit") if tool(t)]
        self.log(f"Scanned {scanned} projects with {', '.join(tools) or 'built-in checks'}. "
                 f"Open: {counts.get('high', 0)} high, {counts.get('medium', 0)} medium, {counts.get('low', 0)} low.")
        if counts.get("high"):
            self.send("manager", f"ALERT: {counts['high']} high-risk security issue(s). See the Security panel.")
        else:
            self.send("manager", "Security scan clean: no high-risk issues.")
        return {"high": counts.get("high", 0), "medium": counts.get("medium", 0),
                "low": counts.get("low", 0), "tools": tools}


if __name__ == "__main__" and "--staged" in sys.argv:
    sys.exit(check_staged())
