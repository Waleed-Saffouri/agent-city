"""Settings for Agent City. Edit these to change how your agents behave."""
import os
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "city.db"

# Your career-ops tracker (used by the Manager for stats). Leave as-is if you don't have it.
TRACKER_PATH = Path(os.environ.get("TRACKER_PATH", Path.home() / "career-ops" / "data" / "applications.md"))

# ---- Model tiering (idea from the "Ultron" clip) ----
# Smart (expensive) model for the manager, cheap model for repetitive workers.
MODELS = {
    "manager": os.environ.get("MANAGER_MODEL", "claude-opus-5-5"),
    "worker": os.environ.get("WORKER_MODEL", "claude-haiku-5-5"),
}

# ---- Token budget (idea from the "AI town" clip) ----
# When the city spends this many tokens in a day, agents go to sleep until tomorrow.
DAILY_TOKEN_BUDGET = int(os.environ.get("DAILY_TOKEN_BUDGET", "200000"))

# ---- Job Scout filters ----
JOB_SOURCES = {
    "new_grad": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "internships": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
}
MAX_AGE_DAYS = 21

TITLE_KEYWORDS = {  # keyword -> points
    "data analyst": 30, "data scientist": 30, "data science": 28, "analytics": 22,
    "business intelligence": 25, "bi ": 15, "quantitative": 20, "financial analyst": 18,
    "risk analyst": 18, "data engineer": 15, "machine learning": 12, "security analyst": 12,
    "cyber": 10, "consultant": 10, "insights": 15, "reporting": 10,
}
SKIP_TITLE = ["senior", "sr.", "staff", "principal", "lead", "manager", "director", "phd", "master's"]

PREFERRED_LOCATIONS = {  # substring -> points
    "new york": 15, "nyc": 15, "remote": 12, "washington": 10, ", va": 10, "virginia": 10,
    "mclean": 10, "arlington": 10, "austin": 10, "durham": 8, "raleigh": 8, "chicago": 6,
    "london": 12,
}

# Companies you don't want (government / defense contractors).
AVOID_COMPANIES = [
    "lockheed", "palantir", "leidos", "caci", "saic", "northrop", "raytheon", "rtx",
    "general dynamics", "bae systems", "booz", "mitre", "peraton", "mantech", "l3harris",
    "anduril", "boeing", "noblis", "parsons", "amentum", "gdit", "federal",
]
