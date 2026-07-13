"""
exporter.py — Stage 6: write the two CSVs sales actually asked for.

helokn_leads.csv          qualified leads, sorted best-first
rejected_candidates.csv   every discard, with the reason — the audit
                          trail for "why only N leads?"
"""

import csv

import config

LEAD_COLUMNS = [
    "company_name", "domain", "website_url", "industry", "icp_category",
    "lead_status", "score", "tier", "trigger_type", "evidence_summary",
    "evidence_url", "contact_status", "contact_name", "contact_title",
    "contact_email", "email_confidence", "contact_source_url",
    "linkedin_search_url", "google_search_url", "serper_source_query",
    "source_result_url", "audit_trail", "rejection_reason", "notes",
]

REJECT_COLUMNS = [
    "raw_title", "raw_url", "domain", "source_query",
    "rejection_reason", "explanation",
]

INDUSTRY_BY_ICP = {
    "Certification Body": "Professional Certification",
    "Credentialing Organization": "Professional Certification",
    "Continuing Education Provider": "Education & Training",
    "Healthcare Training Organization": "Healthcare",
    "Clinical Education Organization": "Healthcare",
    "Nursing Education Organization": "Healthcare",
    "Legal Education / CLE Provider": "Legal",
    "Bar Association": "Legal",
    "Compliance Training Organization": "Compliance & Risk",
    "Workforce Development Organization": "Workforce Development",
    "Corporate L&D Team": "Corporate Learning & Development",
    "Professional Association (Certification)": "Professional Association",
    "Safety Training Organization": "Safety & EHS",
    "Education Organization (Assessments)": "Education & Training",
    "Structured Onboarding / Training Program": "Corporate Learning & Development",
}


def write_leads(leads: list[dict], path=None, run_stamp: str | None = None):
    """Write leads. With run_stamp, writes a per-run timestamped file
    (helokn_leads_YYYYMMDD_HHMM.csv) AND refreshes the stable
    helokn_leads.csv alias — so every run has its own immutable output
    file, while 'the latest leads' is always at a predictable path."""
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    leads = sorted(leads, key=lambda r: (-r["score"], r["tier"], r["company_name"]))
    if path is None:
        path = (config.OUTPUT_DIR / f"helokn_leads_{run_stamp}.csv"
                if run_stamp else config.OUTPUT_DIR / "helokn_leads.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEAD_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(leads)
    if run_stamp:  # refresh the stable alias too
        latest = config.OUTPUT_DIR / "helokn_leads.csv"
        with open(latest, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=LEAD_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(leads)
    return path


def write_rejects(rejects: list[dict], path=None, run_stamp: str | None = None):
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        path = (config.OUTPUT_DIR / f"rejected_candidates_{run_stamp}.csv"
                if run_stamp else config.OUTPUT_DIR / "rejected_candidates.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REJECT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rejects)
    if run_stamp:
        latest = config.OUTPUT_DIR / "rejected_candidates.csv"
        with open(latest, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=REJECT_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rejects)
    return path