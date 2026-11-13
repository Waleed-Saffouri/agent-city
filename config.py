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

# Your applications tracker (Markdown table). Used for stats and so the Scout never suggests a company
# you've already applied to. profile/applications.md is private (git ignores it).
_tracker_default = ROOT / "profile" / "applications.md"
if not _tracker_default.exists():
    _tracker_default = Path.home() / "career-ops" / "data" / "applications.md"
TRACKER_PATH = Path(os.environ.get("TRACKER_PATH", _tracker_default))

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

# Fit score, out of 100. A great match (right title + top city + fresh + right field) lands ~85-95.
#   title up to 35 · location +25 / +15 / 0 / -20 · freshness up to 20 · field up to 10
#   Spring 2027 internship +10 · AI second opinion -15..+15
TITLE_KEYWORDS = {  # keyword -> points
    "data analyst": 35, "data scientist": 35, "data science": 33, "analytics": 28,
    "business intelligence": 30, "bi ": 18, "quantitative": 25, "financial analyst": 25,
    "risk analyst": 25, "data engineer": 20, "machine learning": 18, "security analyst": 18,
    "cyber": 15, "consultant": 15, "insights": 22, "reporting": 15, "investment analyst": 28,
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

# What you bring, so the Scout can skip postings you don't meet on paper.
MY_GPA = float(os.environ.get("MY_GPA", "3.12"))
MAX_YEARS_REQUIRED = 2   # postings that require 3+ years of experience are dropped

# Your two goals:
#   1. a Spring 2027 internship (Jan–May, while you're still a student)
#   2. a full-time job starting after you graduate in May 2027
# Internships for these terms count as goal 1. Summer/fall internships are skipped, because they
# usually require you to still be a student afterwards.
INTERNSHIP_TERMS = ["Spring 2027", "Winter 2027"]
SPRING_BONUS = 10

# Where you'd move. The best location in a posting counts. Anywhere not listed costs points,
# so jobs only in small towns sink to the bottom or drop out.
# Places you can commute to from GMU (Fairfax). Spring internships MUST be here or fully remote,
# because you're still in class until May.
DMV_PLACES = ["washington, dc", "washington dc", ", dc", "district of columbia", "arlington", "mclean",
              "reston", "tysons", "fairfax", "alexandria", "herndon", "vienna, va", "chantilly",
              "falls church", "ashburn", "sterling, va", "manassas", "leesburg", "centreville",
              "springfield, va", "annandale", "bethesda", "rockville", "silver spring", "gaithersburg",
              "college park", "dmv"]
LOCATION_TIERS = {
    25: ["new york", "nyc", "manhattan", "brooklyn", "jersey city", "hoboken"] + DMV_PLACES,
    15: ["austin", "dallas", "houston", "plano", "irving", "fort worth", "frisco",             # Texas
         "raleigh", "durham", "charlotte", "chapel hill", "research triangle", "cary, nc",    # NC
         "remote in usa", "remote in us", "us remote", "remote - us", "remote, us", "london", "chicago", "boston",
         ", ny", "long island", "stamford", "newark", "baltimore", "columbia, md"],                # near NYC / DC
    0:  ["richmond", "philadelphia", "atlanta", "miami", "seattle", "san francisco", "los angeles", "denver",
         "united states", "remote"],   # plain "remote" with no country: neutral
}
OTHER_LOCATION_POINTS = -20

# Companies you don't want (government / defense contractors).
AVOID_COMPANIES = [
    "lockheed", "palantir", "leidos", "caci", "saic", "northrop", "raytheon", "rtx",
    "general dynamics", "bae systems", "booz", "mitre", "peraton", "mantech", "l3harris",
    "anduril", "boeing", "noblis", "parsons", "amentum", "gdit", "federal", "steampunk", "guidehouse",
    "maximus", "accenture federal", "deloitte government", "cgi federal", "icf",
]

# ---- Sentinel (security) ----
# Folders Sentinel scans. Agent City itself is always included.
SENTINEL_TARGETS = [p for p in os.environ.get("SENTINEL_TARGETS", str(Path.home() / "claude_code")).split(":") if p]

# What each agent is allowed to do (the "StarNet" permissions idea). This is ENFORCED:
# agents reach the internet only through Agent.fetch() and the AI only through Agent.think(),
# both of which check this list and log anything blocked. Sentinel reports blocked attempts.
PERMISSIONS = {
    "manager":  {"network": ["api.telegram.org"], "ai": ["think"]},
    "scout":    {"network": ["raw.githubusercontent.com", "boards-api.greenhouse.io",
                             # job posting pages, read only to find the salary:
                             "boards.greenhouse.io", "job-boards.greenhouse.io", "*.lever.co",
                             "jobs.ashbyhq.com", "apply.workable.com", "ats.rippling.com", "*.myworkdayjobs.com", "jobs.smartrecruiters.com",
                             "*.icims.com", "*.oraclecloud.com"],
                 "ai": ["think"]},
    "sentinel": {"network": [], "ai": []},   # scanners run locally; Sentinel never needs AI
    "writer":   {"network": ["boards-api.greenhouse.io"], "ai": ["think"]},
    "planner":  {"network": ["api.telegram.org"], "ai": ["think"]},
    "school":   {"network": ["canvas.gmu.edu", "*.instructure.com"], "ai": []},
    "applier":  {"network": [], "ai": ["think"]},   # drives a visible browser you watch; a guard blocks Submit
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
# So it doesn't over-text you: at most this many messages a day, and none during quiet hours
# (reminders that come due during quiet hours wait until morning).
PHONE_MAX_PER_DAY = int(os.environ.get("PHONE_MAX_PER_DAY", "5"))
PHONE_QUIET_HOURS = (22, 8)   # 10pm to 8am

# ---- School (Canvas) ----
# Canvas → Calendar → "Calendar Feed" (bottom right) → copy the link, and put it in .env as
#   CANVAS_ICS_URL=https://canvas.gmu.edu/feeds/calendars/user_....ics
# It's read-only, but treat it like a password: anyone with it can see your course calendar.
CANVAS_ICS_URL = os.environ.get("CANVAS_ICS_URL", "")
