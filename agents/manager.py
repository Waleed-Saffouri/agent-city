"""The Manager: reads the message board and everyone's results, writes the morning brief,
watches for crashed agents, and answers your questions in chat."""
import re
import time

import db
from agents.base import Agent
from config import TRACKER_PATH


def tracker_stats():
    """Counts applications by status from the career-ops tracker (if it exists)."""
    if not TRACKER_PATH.exists():
        return None
    counts = {}
    for line in TRACKER_PATH.read_text().splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 6 and cells[1].isdigit():
            counts[cells[6]] = counts.get(cells[6], 0) + 1
    return counts


class Manager(Agent):
    name = "manager"
    tier = "manager"
    role = "Runs the city: morning brief, delegates work, steps in when an agent breaks."

    def gather(self):
        since = time.time() - 86400
        return {
            "messages": db.rows("SELECT sender, text FROM messages WHERE recipient='manager' AND ts > ? ORDER BY ts", (since,)),
            "pending": db.rows("SELECT company, title, score, track, salary FROM jobs WHERE status='pending' ORDER BY score DESC LIMIT 5"),
            "top_spring_internships": db.rows("""SELECT company, title, score, salary FROM jobs WHERE status='pending'
                                                 AND track='internship' ORDER BY score DESC LIMIT 3"""),
            "top_fulltime_jobs": db.rows("""SELECT company, title, score, salary FROM jobs WHERE status='pending'
                                            AND track='fulltime' ORDER BY score DESC LIMIT 3"""),
            "pending_count": db.rows("SELECT COUNT(*) n FROM jobs WHERE status='pending'")[0]["n"],
            "errors": db.rows("SELECT agent FROM agent_state WHERE status='error'"),
            "tracker": tracker_stats(),
            "tokens": db.tokens_today(),
            "todos": __import__("agents.planner", fromlist=["Planner"]).Planner().due_summary(),
            "drafts_ready": db.rows("SELECT COUNT(*) n FROM drafts WHERE ts > ?", (since,))[0]["n"],
            "security": {r["severity"]: r["n"] for r in db.rows(
                "SELECT severity, COUNT(*) n FROM findings WHERE status='open' GROUP BY severity")},
        }

    def work(self):
        g = self.gather()
        # Step in when something breaks (from the "Ultron" clip).
        for e in g["errors"]:
            self.log(f"{e['agent']} is down. Restarting it once.", "alert")
            if self.restart(e["agent"]):
                self.log(f"{e['agent']} is back up.")
        g = self.gather()  # refresh after any restarts

        prompt = ("You are the manager of Waleed's personal AI agent city. Write a short, friendly "
                  "morning brief (under 170 words, bullet points) from this data. He has two goals: a Spring 2027 "
                  "internship and a full-time job after May 2027, so list the best of each separately (with pay "
                  f"when known). Put overdue and due-today to-dos at the very top. Highlight what needs his approval.\n\nDATA: {g}")
        brief = self.think(prompt) or self.template_brief(g)
        db.execute("INSERT INTO briefs(ts, text) VALUES (?,?)", (time.time(), brief))
        self.send("all", "Morning brief is ready.")
        if self.notify("Agent City morning brief\n\n" + brief.replace("**", "")):
            self.log("Sent the brief to your phone.")
        return {"pending": g["pending_count"]}

    def restart(self, name):
        """Ultron move: when a worker crashes, try it again once before bothering you."""
        from agents.job_scout import JobScout
        from agents.sentinel import Sentinel
        from agents.writer import Writer
        cls = {"scout": JobScout, "sentinel": Sentinel, "writer": Writer}.get(name)
        if not cls:
            return False
        try:
            cls().run()
            return True
        except Exception:
            return False

    @staticmethod
    def template_brief(g):
        lines = ["Good morning, Waleed. Here's your city today:"]
        if g["pending_count"]:
            lines.append(f"• {g['pending_count']} job matches waiting for your approval. Best ones:")
            lines += [f"   – {p['company']}: {p['title']} (fit {p['score']})" for p in g["pending"][:3]]
        else:
            lines.append("• No new job matches waiting.")
        if g["tracker"]:
            t = g["tracker"]
            lines.append("• Applications: " + ", ".join(f"{v} {k.lower()}" for k, v in t.items()))
        sec = g.get("security") or {}
        if sec.get("high"):
            lines.append(f"• 🚨 Sentinel found {sec['high']} high-risk security issue(s). Check the Security panel.")
        elif sec:
            lines.append(f"• Security: no high-risk issues ({sec.get('medium', 0)} medium, {sec.get('low', 0)} low).")
        else:
            lines.append("• Security: all clear.")
        for e in g["errors"]:
            lines.append(f"• ⚠️ {e['agent']} crashed and needs a look.")
        lines.append(f"• Tokens used today: {g['tokens']:,}")
        return "\n".join(lines)

    def chat(self, question):
        # HQ can act, not just answer: to-dos go to the Planner, "run X" starts an agent.
        q = question.strip()
        if re.match(r"(?i)(remind me|todo|to-do|to do:|add (a )?(task|to-?do)|don'?t (let me )?forget|note:)", q):
            from agents.planner import Planner
            n = Planner().capture(q)
            return f"Added {n} item{'s' if n != 1 else ''} to your to-dos. Open the Planner to see them."
        m = re.match(r"(?i)(?:run|start|check)\s+(?:the\s+)?(scout|sentinel|writer|planner|jobs|security)\b", q)
        if m:
            import threading
            from agents.job_scout import JobScout
            from agents.planner import Planner
            from agents.sentinel import Sentinel
            from agents.writer import Writer
            name = {"jobs": "scout", "security": "sentinel"}.get(m[1].lower(), m[1].lower())
            cls = {"scout": JobScout, "sentinel": Sentinel, "writer": Writer, "planner": Planner}[name]
            threading.Thread(target=cls().run, daemon=True).start()
            return f"Started the {name}. Watch its building light up; results land in its panel."
        g = self.gather()
        answer = self.think(f"You manage Waleed's AI agent city. Answer briefly and plainly from the data. "
                            "If he asks for something only a code change can do, say so in one line and "
                            "suggest he ask Claude to build it.\n"
                            f"City data: {g}\n\nWaleed asks: {question}")
        if answer:
            return answer
        # Simple rule-based answers when no AI key is set.
        q = question.lower()
        if re.search(r"job|match|approv", q):
            return self.template_brief(g)
        if "budget" in q or "token" in q:
            return f"We've used {g['tokens']:,} tokens today."
        return ("I don't have an AI key yet, so I can only answer about jobs, approvals and budget. "
                "Add ANTHROPIC_API_KEY to talk to me properly.")
