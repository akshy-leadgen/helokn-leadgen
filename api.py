"""
api.py — hardened HTTP wrapper around the pipeline, for Cloud Run.

Endpoints:
  GET  /health   liveness check (no auth)
  POST /run      {"mode": "test" | "full"}  -> starts a pipeline run in
                 the background, pushes results to Supabase when done.
                 Requires header:  x-api-key: <RUN_API_KEY>

Security:
  * /run is protected by an API key read from the RUN_API_KEY env var.
    Without this, anyone who found the URL could burn your Serper
    credits by hammering the endpoint.
  * All secrets (SERPER_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY,
    RUN_API_KEY) come from environment variables set on the Cloud Run
    service — nothing is baked into the image.

CORS:
  The Lovable dashboard calls POST /run from the browser, which requires
  CORS headers. Locked to *.lovable.app plus localhost for testing; add
  your custom domain to ALLOWED_ORIGIN_REGEX if you attach one later.

Cloud Run note (important):
  The pipeline runs as a BACKGROUND task after /run returns. Cloud Run
  throttles CPU to ~0 after the response unless the service is deployed
  with CPU always allocated. Deploy with `--no-cpu-throttling` (the
  DEPLOY_GUIDE does this) or the background run will stall silently.
"""

import os
import secrets
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, BackgroundTasks, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import supabase_export

APP_DIR = Path(__file__).resolve().parent
RUN_API_KEY = os.environ.get("RUN_API_KEY", "")

app = FastAPI(title="Helokn Lead Gen API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*\.lovable\.app|http://localhost:\d+",
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


class RunRequest(BaseModel):
    mode: str = "test"          # "test" or "full"
    triggered_by: str = "dashboard"


def _check_key(x_api_key: str | None):
    if not RUN_API_KEY:
        raise HTTPException(500, "RUN_API_KEY is not configured on the server.")
    if not x_api_key or not secrets.compare_digest(x_api_key, RUN_API_KEY):
        raise HTTPException(401, "Invalid or missing x-api-key header.")


@app.get("/health")
def health():
    return {"status": "ok"}


def _run_pipeline(mode: str, triggered_by: str, run_id: str):
    sb = supabase_export.get_client()
    try:
        sb.table("pipeline_runs").upsert({
            "run_id": run_id, "status": "running",
            "triggered_by": triggered_by,
        }).execute()

        cmd = ["python", "run.py"] + (["--full"] if mode == "full" else [])
        subprocess.run(cmd, check=True, cwd=APP_DIR, timeout=60 * 45)

        # run.py stamps output files with its own timestamp; find the
        # newest pair rather than assuming run_id matches to the minute.
        out = APP_DIR / "output"
        leads = max(out.glob("helokn_leads_*.csv"), key=lambda p: p.stat().st_mtime)
        rejects = max(out.glob("rejected_candidates_*.csv"),
                      key=lambda p: p.stat().st_mtime)
        supabase_export.push_run(str(leads), str(rejects), run_id, triggered_by)
    except Exception as e:
        # Record the failure so the dashboard's "last run" status is honest
        # instead of showing "running" forever.
        try:
            sb.table("pipeline_runs").update({
                "status": "failed", "error_message": str(e)[:1000],
                "finished_at": "now()",
            }).eq("run_id", run_id).execute()
        except Exception:
            pass
        raise


@app.post("/run")
def run_pipeline(req: RunRequest, background_tasks: BackgroundTasks,
                 x_api_key: str | None = Header(default=None)):
    _check_key(x_api_key)
    if req.mode not in ("test", "full"):
        raise HTTPException(400, "mode must be 'test' or 'full'")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    background_tasks.add_task(_run_pipeline, req.mode, req.triggered_by, run_id)
    return {"status": "started", "mode": req.mode, "run_id": run_id}