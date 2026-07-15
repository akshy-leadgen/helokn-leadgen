"""
supabase_export.py — pushes the pipeline's CSV output into Supabase.

Run after run.py finishes (or call push_run() directly from run.py).
Requires SUPABASE_URL and SUPABASE_SERVICE_KEY in .env (service key,
not anon key — this writes data, so it needs to bypass RLS as the
pipeline's own trusted process).

    pip install supabase
    python supabase_export.py output/helokn_leads_20260713_1224.csv \
                              output/rejected_candidates_20260713_1224.csv \
                              --run-id 20260713_1224
"""

import argparse
import csv
import os
import sys
from pathlib import Path

from supabase import create_client

BASE_DIR = Path(__file__).resolve().parent


def load_env(path: Path = BASE_DIR / ".env"):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def get_client():
    load_env()
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


def push_run(leads_csv: str, rejects_csv: str, run_id: str,
            triggered_by: str = "manual"):
    sb = get_client()

    sb.table("pipeline_runs").upsert({
        "run_id": run_id, "status": "running", "triggered_by": triggered_by,
    }).execute()

    leads = list(csv.DictReader(open(leads_csv, encoding="utf-8")))
    for row in leads:
        row["run_id"] = run_id
    if leads:
        # chunk to stay under request size limits
        for i in range(0, len(leads), 200):
            sb.table("leads").upsert(
                leads[i:i + 200], on_conflict="run_id,domain"
            ).execute()

    rejects = list(csv.DictReader(open(rejects_csv, encoding="utf-8")))
    for row in rejects:
        row["run_id"] = run_id
    if rejects:
        for i in range(0, len(rejects), 200):
            sb.table("rejected_candidates").insert(rejects[i:i + 200]).execute()

    sb.table("pipeline_runs").update({
        "status": "completed", "leads_count": len(leads),
        "rejects_count": len(rejects), "finished_at": "now()",
    }).eq("run_id", run_id).execute()

    print(f"Pushed {len(leads)} leads and {len(rejects)} rejects "
         f"(run_id={run_id}) to Supabase.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("leads_csv")
    p.add_argument("rejects_csv")
    p.add_argument("--run-id", required=True)
    p.add_argument("--triggered-by", default="manual")
    args = p.parse_args()
    push_run(args.leads_csv, args.rejects_csv, args.run_id, args.triggered_by)