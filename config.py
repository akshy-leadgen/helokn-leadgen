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
# institutional verticals. v7: raised 80 -> 95 after inserting 14 K-12/
# teacher discovery queries into the GUARANTEED primary set (bumping
# PRODUCTION_QUERY_COUNT 45 -> 59) — everything that used to start at
# primary position 45 now starts at 59, so the budget needs to stretch
# further to give those categories the same top-up headroom as before.
# The disk cache makes previously-searched queries free on every
# subsequent run, so raising the cap only spends credits on genuinely new
# queries. Override with --max-queries.
MAX_SERPER_QUERIES = 95       # hard cap on *fresh* Serper calls per run
MAX_RESULTS_PER_QUERY = 10    # Serper 'num'. NOTE: confirmed via testing that
                              # this account's plan hard-caps num at 10 —
                              # anything above returns HTTP 400 ("Query
                              # pattern not allowed for free accounts").
                              # Raise this ONLY if you upgrade your Serper
                              # plan and confirm a higher num works first.
MAX_CONTACTS_PER_COMPANY = 1  # one best contact per company by default
                              # (K-12 Education (Teachers) leads are the
                              # exception — see MAX_TEACHER_CONTACTS_PER_SCHOOL)
MAX_TEACHER_CONTACTS_PER_SCHOOL = 5  # up to N individual teacher emails per
                              # school, since "find teacher emails" means
                              # plural educators, not one org gatekeeper
TARGET_LEADS = 100            # goal for a production run
MAX_RUN_MINUTES = 25          # hard wall-clock budget for a --full run. This
                              # is an ENFORCED stop, not a hope: once reached,
                              # the pipeline stops starting new work and
                              # exports whatever's completed so far, rather
                              # than running indefinitely and relying on an
                              # external process getting killed. Leaves a
                              # 5-min margin under the "30 minutes at the
                              # most" target.

# Test mode (the default) processes exactly this much:
TEST_MODE_QUERIES = 1
TEST_MODE_LEADS = 1

# Network behavior for free (non-Serper) website fetches
HTTP_TIMEOUT = 10           # homepage validation fetches (accuracy matters)
CONTACT_HTTP_TIMEOUT = 4    # contact-page fetches (speed matters; a miss
                            # here degrades one field, not the whole lead).
                            # Lowered 5->4 as part of getting a full run
                            # back under 30 min — most real pages that will
                            # ever respond do so well under 4s; a slow-but-
                            # real page beyond that is a rare trade worth
                            # making for materially lower worst-case time
                            # across hundreds of contact-page attempts.
HTTP_RETRIES = 1
MAX_CONTACT_PAGES = 12      # max contact/about/team/staff/faculty paths
                            # tried per company. Brought back down from 16:
                            # combined with K-12 leads now doing up to TWO
                            # passes (teacher scan, then org-level fallback
                            # if empty), 16 was a major contributor to runs
                            # exceeding the 30-min target. 12 still covers
                            # the highest-value paths (reordered earlier so
                            # people/staff/faculty pages come first) while
                            # meaningfully cutting worst-case time per company.
MAX_WORKERS = 20            # parallel threads for validate+enrich. Raised
                            # 10 -> 20: this work is network-bound (waiting
                            # on other people's websites), so more threads
                            # translate almost directly into wall-clock
                            # speedup, not more load on any single site
                            # (each thread hits a DIFFERENT company).
                            # Combined with the MAX_RUN_MINUTES budget above,
                            # this is what makes the 30-min target realistic
                            # for the larger v7 query set instead of just
                            # hoping the run finishes in time.
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
    "K-12 Education (Teachers)",
]