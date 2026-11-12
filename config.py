"""Settings for Agent City. Edit these to change how your agents behave."""
import os
from pathlib import Path

ROOT = Path(__file__).parent

# Load secrets from a local .env file (git ignores it). Format: NAME=value, one per line.
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
DB_PATH = ROOT / "data" / "city.db"

# Your career-ops tracker (used by the Manager for stats). Leave as-is if you don't have it.
TRACKER_PATH = Path(os.environ.get("TRACKER_PATH", Path.home() / "career-ops" / "data" / "applications.md"))

# ---- Model tiering (idea from the "Ultron" clip) ----
# Smart (expensive) model for the manager, cheap model for repetitive workers.
#   rules   = no AI at all (free, instant): filtering, scoring, scanning
#   worker  = Haiku (cheapest Claude) or a free local model through Ollama
#   manager = Sonnet for the daily brief and chat
#   deep    = Opus, only for rare heavy jobs (e.g. a weekly review), never by default
MODELS = {
    "deep": os.environ.get("DEEP_MODEL", "claude-opus-5-5"),
    "manager": os.environ.get("MANAGER_MODEL", "claude-sonnet-5-5"),
    "worker": os.environ.get("WORKER_MODEL", "claude-haiku-5-5"),
}
CLI_MODELS = {"deep": "opus", "manager": "sonnet", "worker": "haiku"}  # names Claude Code understands
# Free local model for workers, if Ollama is installed (https://ollama.com). Set to "" to disable.
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")

# ---- Token budget (idea from the "AI town" clip) ----
# When the city spends this many tokens in a day, agents go to sleep until tomorrow.
DAILY_TOKEN_BUDGET = int(os.environ.get("DAILY_TOKEN_BUDGET", "200000"))

# ---- Job Scout filters ----
JOB_SOURCES = {
    "new_grad": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "internships": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
}
# Company job boards on Greenhouse (public, no login). Mostly finance, trading and fintech firms that
# the SimplifyJobs lists under-cover. Add a company by its Greenhouse board name
# (the word after greenhouse.io/ in its job links).
GREENHOUSE_COMPANIES = [
    "point72", "schonfeld", "drweng", "imc", "janestreet", "jumptrading", "towerresearchcapital",
    "akunacapital", "pdtpartners", "virtu", "clearstreet", "stripe", "brex", "affirm", "coinbase",
    "carta", "chime", "sofi", "robinhood", "betterment", "mercury", "vestwell", "figma", "datadog",
    "mongodb", "airbnb", "asana",
]
MAX_AGE_DAYS = 21

TITLE_KEYWORDS = {  # keyword -> points
    "data analyst": 30, "data scientist": 30, "data science": 28, "analytics": 22,
    "business intelligence": 25, "bi ": 15, "quantitative": 20, "financial analyst": 18,
    "risk analyst": 18, "data engineer": 15, "machine learning": 12, "security analyst": 12,
    "cyber": 10, "consultant": 10, "insights": 15, "reporting": 10,
}
SKIP_TITLE = ["senior", "sr.", "staff", "principal", "lead", "manager", "director", "phd", "master's",
              "apprentice", "postdoc"]

# Words that mean "not your field" even when the title says data/analyst. Each costs points.
OFF_TOPIC = {
    "nephrology": 25, "clinical": 15, "transplant": 25, "nurse": 25, "patient": 15, "laboratory": 15,
    "lab ": 10, "biolog": 15, "chemist": 20, "robotics": 15, "collection operator": 30,
    "hardware": 15, "embedded": 15, "greenhouse gas": 10, "supply chain": 5,
}
# Simplify's job categories that fit you best.
GOOD_CATEGORIES = {"AI/ML/Data": 8, "Quant": 10, "Product": 0}

# You graduate in May 2027, so summer-2027 internships (which usually need you to still be a
# student afterwards) are worth less than full-time roles. Spring/winter/co-op ones are fine.
INTERNSHIP_PENALTY = 15

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

# ---- Sentinel (security) ----
# Folders Sentinel scans. Agent City itself is always included.
SENTINEL_TARGETS = [p for p in os.environ.get("SENTINEL_TARGETS", str(Path.home() / "claude_code")).split(":") if p]

# What each agent is allowed to do (the "StarNet" permissions idea). This is ENFORCED:
# agents reach the internet only through Agent.fetch() and the AI only through Agent.think(),
# both of which check this list and log anything blocked. Sentinel reports blocked attempts.
PERMISSIONS = {
    "manager":  {"network": ["api.telegram.org"], "ai": ["think"]},
    "scout":    {"network": ["raw.githubusercontent.com", "boards-api.greenhouse.io"], "ai": ["think"]},
    "sentinel": {"network": [], "ai": []},   # scanners run locally; Sentinel never needs AI
    "writer":   {"network": ["boards-api.greenhouse.io"], "ai": ["think"]},
}

# ---- Writer ----
# Your CV in Markdown: the Writer's ONLY source of facts. Kept out of git (it has your contact info).
CV_PATH = Path(os.environ.get("CV_PATH", ROOT / "profile" / "cv.md"))
PROFILE_NAME = os.environ.get("PROFILE_NAME", "Waleed Saffouri")
DRAFTS_DIR = ROOT / "data" / "drafts"

# ---- Phone alerts (optional) ----
# Same kind of Telegram bot your trading bot uses. Put these two in a .env file (never in code):
#   TELEGRAM_BOT_TOKEN=...   TELEGRAM_CHAT_ID=...
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
