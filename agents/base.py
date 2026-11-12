"""What every agent has in common: a name, a tier, a log, a voice on the message board,
and (optionally) a connection to Claude."""
import os
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

    # ---- Optional AI brain. Works without an API key (agents fall back to rules). ----
    def think(self, prompt, max_tokens=800):
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return None
        if db.tokens_today() >= DAILY_TOKEN_BUDGET:
            self.log("Daily token budget used up. Sleeping until tomorrow.", "budget")
            return None
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
