# Agent City

A pixel-art city where each building is an AI agent working for me. Every morning one agent finds jobs that fit me, another checks my code for security problems, and a manager agent reads both reports and writes me a short brief.

![Agent City](docs/city.png)

## Why I built it
I was applying to jobs by hand and wanted a system that does the searching and checking for me while I make the final decisions. I also wanted to see how multi-agent systems work in practice: how agents talk to each other, how to keep their costs down, and how to keep them from doing things they shouldn't.

## The agents
| Building | Agent | What it does | Uses AI? |
|---|---|---|---|
| HQ | **Manager** | Reads the message board and every agent's results, writes the morning brief, answers questions in chat, flags crashed agents | Yes (mid-tier model) |
| Scout | **Job Scout** | Pulls ~2,000 fresh postings from the SimplifyJobs lists, filters and scores them, learns from my approve/skip clicks, and queues the best ones | Rules + one batched AI re-rank per day |
| Sentinel | **Sentinel** | Scans my projects for leaked API keys, committed `.env` files, Python packages with known CVEs, and risky code; also watches the agents for runaway token use | No (free) |

Agents communicate through a shared **message board**. Everything they do is written to an **activity log**, and the city animates in real time: windows light up while an agent works, messages travel between buildings, and HQ flashes red when Sentinel finds a high-risk issue.

## How it works
```
 run.py morning / 7:00 scheduler / "Run" button
        │
        ▼
 Job Scout ──┐         Sentinel ──┐
  rules →    │  message  gitleaks │
  learning → ├─ board ─► pip-audit├──► Manager ──► Morning brief + chat
  AI re-rank │  (SQLite) patterns │
             ▼                    ▼
        jobs table          findings table
                     │
          FastAPI server ──► pixel city dashboard (canvas, polls every 3s)
```

**Design choices**
- **Cheapest tool first.** Filtering, scoring and security scanning are plain code. AI is only used where judgment helps, and the Scout sends all its top matches in *one* request instead of one per job.
- **Model tiers.** Workers use the cheapest model (or a free local model through Ollama), the manager uses a mid-tier model, and the most expensive model is reserved for rare heavy jobs.
- **No paid API required.** The AI layer runs through the Claude Code CLI on my existing subscription; an API key or no AI at all also work, and every agent has a rule-based fallback.
- **Daily token budget.** When the city hits its budget, agents stop calling the AI until tomorrow.
- **Human in the loop.** The Scout queues jobs; nothing is applied to without my click.
- **Agent permissions.** Each agent has a declared list of what it may touch (see `PERMISSIONS` in `config.py`).
- **Secrets never stored.** Sentinel records *where* a secret is and what kind, never its value.

## Results so far
- First run: the Scout checked **2,245** active postings and queued **37–41** matches after filtering out defense contractors, senior roles, off-topic fields and companies I've already applied to.
- Sentinel, tested on a deliberately broken project, caught a planted API key, a committed `.env`, 3 outdated packages with known vulnerabilities, a default password and disabled TLS checks.
- A full morning run (all three agents) used about **5,000 tokens**.

## Limitations and next steps
- Approving a job doesn't yet generate the tailored CV and cover letter (next).
- Phone alerts through Telegram.
- Inbox, School and Trading Tower buildings are placeholders.
- Runs on my laptop; a GitHub Actions version would let it run while the laptop is closed.

## Setup
```bash
git clone https://github.com/Waleed-Saffouri/agent-city.git
cd agent-city
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py morning          # run all agents once
uvicorn server:app             # open http://localhost:8000
```
Optional: install [Claude Code](https://claude.com/claude-code) and log in for the AI layer, [Ollama](https://ollama.com) for free local workers, and `gitleaks` + `pip-audit` for deeper security scans. Run tests with `python -m pytest -q`.

## Tech stack
Python · FastAPI · SQLite · HTML canvas · Claude (via Claude Code CLI or API) · Ollama · gitleaks · pip-audit · pytest
