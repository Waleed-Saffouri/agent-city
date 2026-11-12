"""What every agent has in common: a name, a tier, a log, a voice on the message board,
and (optionally) a connection to Claude."""
import json
import os
import shutil
import subprocess
import time

import db
from config import DAILY_TOKEN_BUDGET, MODELS


class Agent:
    name = "agent"
    tier = "worker"          # "manager" uses the smart model, "worker" the cheap one
    role = ""                # one-line job description shown in the city

    def log(self, text, kind="info"):
        db.log(self.name, kind, text)

    def send(self, to, text):
        db.send(self.name, to, text)

    def inbox(self, since=0):
        return db.rows("SELECT * FROM messages WHERE recipient IN (?, 'all') AND ts > ? ORDER BY ts",
                       (self.name, since))

    # ---- AI brain. Three options, picked automatically:
    #   1. "claude-cli": the Claude Code app logged in with your Claude subscription (no extra cost)
    #   2. "api": an ANTHROPIC_API_KEY (pay per token)
    #   3. none: agents fall back to simple rules
    def brain(self):
        choice = os.environ.get("BRAIN", "auto")
        if choice in ("auto", "claude-cli") and shutil.which("claude"):
            return "claude-cli"
        if choice in ("auto", "api") and os.environ.get("ANTHROPIC_API_KEY"):
            return "api"
        return None

    def think(self, prompt, max_tokens=800):
        brain = self.brain()
        if not brain:
            return None
        if db.tokens_today() >= DAILY_TOKEN_BUDGET:
            self.log("Daily token budget used up. Sleeping until tomorrow.", "budget")
            return None
        if brain == "claude-cli":
            return self._think_cli(prompt)
        key = os.environ["ANTHROPIC_API_KEY"]
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=key)
            model = MODELS[self.tier]
            msg = client.messages.create(model=model, max_tokens=max_tokens,
                                         messages=[{"role": "user", "content": prompt}])
            used = msg.usage.input_tokens + msg.usage.output_tokens
            db.execute("INSERT INTO token_usage(ts, agent, model, tokens) VALUES (?,?,?,?)",
                       (time.time(), self.name, model, used))
            return msg.content[0].text
        except Exception as e:  # never let one agent crash the city
            self.log(f"AI call failed: {e}", "error")
            return None

    def _think_cli(self, prompt):
        """Ask Claude through the Claude Code command line (`claude -p`), using your subscription."""
        alias = {"manager": "opus", "worker": "haiku"}[self.tier]
        try:
            out = subprocess.run(
                ["claude", "-p", prompt, "--model", alias, "--output-format", "json"],
                capture_output=True, text=True, timeout=300)
            data = json.loads(out.stdout)
            u = data.get("usage", {})
            used = sum(u.get(k, 0) or 0 for k in ("input_tokens", "output_tokens"))
            db.execute("INSERT INTO token_usage(ts, agent, model, tokens) VALUES (?,?,?,?)",
                       (time.time(), self.name, f"cli:{alias}", used))
            return data.get("result") or None
        except Exception as e:
            self.log(f"Claude Code call failed: {e}", "error")
            return None

    def run(self):
        db.set_state(self.name, "working")
        self.log("Started work.", "start")
        try:
            result = self.work()
            db.set_state(self.name, "idle", result)
            self.log("Finished.", "done")
            return result
        except Exception as e:
            db.set_state(self.name, "error", {"error": str(e)})
            self.log(f"Crashed: {e}", "error")
            db.send(self.name, "manager", f"I hit an error and stopped: {e}")
            raise

    def work(self):
        raise NotImplementedError
