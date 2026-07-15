"""
api.py — thin HTTP wrapper around the existing pipeline, for Cloud Run.

Gives the Lovable dashboard something to call:
  POST /run  {"mode": "test" | "full"}   -> kicks off a pipeline run,
                                            pushes results to Supabase
  GET  /health                            -> liveness check

This does NOT replace run.py — it shells out to it. All the actual
lead-gen logic stays exactly as-is; this file's only job is "let a web
button trigger what you'd otherwise type in a terminal."

Bring this into Antigravity to harden it (background job queue instead
of blocking the request, auth on the /run endpoint, retry handling) and
deploy it — Antigravity has a first-party Cloud Run deploy flow.
"""

import subprocess
import uuid
from datetime import datetime

from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel

import supabase_export

app = FastAPI(title="Helokn Lead Gen API")


class RunRequest(BaseModel):
    mode: str = "test"          # "test" or "full"
    triggered_by: str = "dashboard"


@app.get("/health")
def health():
    return {"status": "ok"}


def _run_pipeline(mode: str, triggered_by: str):
    run_id = datetime.now().strftime("%Y%m%d_%H%M")
    cmd = ["python", "run.py"] + (["--full"] if mode == "full" else [])
    subprocess.run(cmd, check=True, cwd="/app")   # adjust cwd for your image
    leads_csv = f"/app/output/helokn_leads_{run_id}.csv"
    rejects_csv = f"/app/output/rejected_candidates_{run_id}.csv"
    supabase_export.push_run(leads_csv, rejects_csv, run_id, triggered_by)


@app.post("/run")
def run_pipeline(req: RunRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_pipeline, req.mode, req.triggered_by)
    return {"status": "started", "mode": req.mode}