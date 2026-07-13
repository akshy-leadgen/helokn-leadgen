"""
config.py — all knobs in one place.

Loads SERPER_API_KEY from .env (tiny built-in parser, no dependency).
Everything that controls cost or volume lives here so a reviewer can
audit the Serper budget at a glance.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
OUTPUT_DIR = BASE_DIR / "output"


def load_env(path: Path = BASE_DIR / ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments. No deps."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_env()

SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")
SERPER_ENDPOINT = "https://google.serper.dev/search"

# ---------------------------------------------------------------------------
# Budget / volume knobs (overridable via CLI flags in run.py)
# ---------------------------------------------------------------------------
# v2: raised 30 -> 65 after the v1 audit (discovery gap in several ICP
# categories). v3: raised 65 -> 80 with 14 new top-up queries in untapped
# institutional verticals (fire/police academies, banking/insurance
# institutes, trades, aviation, franchises). The disk cache makes
# previously-searched queries free on every subsequent run, so raising the
# cap only spends credits on the NEW queries. Override with --max-queries.
MAX_SERPER_QUERIES = 80       # hard cap on *fresh* Serper calls per run
MAX_RESULTS_PER_QUERY = 10    # Serper 'num'. NOTE: confirmed via testing that
                              # this account's plan hard-caps num at 10 —
                              # anything above returns HTTP 400 ("Query
                              # pattern not allowed for free accounts").
                              # Raise this ONLY if you upgrade your Serper
                              # plan and confirm a higher num works first.
MAX_CONTACTS_PER_COMPANY = 1  # one best contact per company by default
TARGET_LEADS = 100            # goal for a production run

# Test mode (the default) processes exactly this much:
TEST_MODE_QUERIES = 1
TEST_MODE_LEADS = 1

# Network behavior for free (non-Serper) website fetches
HTTP_TIMEOUT = 10           # homepage validation fetches (accuracy matters)
CONTACT_HTTP_TIMEOUT = 5    # contact-page fetches (speed matters; a miss
                            # here degrades one field, not the whole lead)
HTTP_RETRIES = 1
MAX_CONTACT_PAGES = 8       # max contact/about/team paths tried per company
                            # (was unbounded at 17 — the dominant cost of a
                            # 2-hour production run)
MAX_WORKERS = 10            # parallel threads for validate+enrich. The work
                            # is network-bound (waiting on other people's
                            # websites), so threads give a near-linear
                            # speedup: ~2h sequential -> ~10-15 min.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 HeloknLeadBot/1.0"
)

# ---------------------------------------------------------------------------
# ICP categories — the only allowed values for the icp_category column.
# Misclassification in the old pipeline came from free-text categories.
# ---------------------------------------------------------------------------
ICP_CATEGORIES = [
    "Certification Body",
    "Credentialing Organization",
    "Continuing Education Provider",
    "Healthcare Training Organization",
    "Clinical Education Organization",
    "Nursing Education Organization",
    "Legal Education / CLE Provider",
    "Bar Association",
    "Compliance Training Organization",
    "Workforce Development Organization",
    "Corporate L&D Team",
    "Professional Association (Certification)",
    "Safety Training Organization",
    "Education Organization (Assessments)",
    "Structured Onboarding / Training Program",
]