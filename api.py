"""
api.py — hardened HTTP wrapper around the pipeline, for Render/Cloud Run.

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
    RUN_API_KEY) come from environment variables set on the host —
    nothing is baked into the image.

CORS:
  The Lovable dashboard calls POST /run from the browser, which requires
  CORS headers. Locked to *.lovable.app plus localhost for testing; add
  your custom domain to ALLOWED_ORIGIN_REGEX if you attach one later.

IMPORTANT — free-tier idle spin-down (this bit us in production):
  Render's (and similarly, some Cloud Run configs') free tier spins the
  container down after ~15 min with no INCOMING HTTP traffic — even if
  a background task is still actively running inside it. Since /run
  returns immediately and the pipeline keeps working in the background,
  a run with no further requests hitting the service gets silently
  killed mid-flight: no error, no traceback, the pipeline_runs row just
  stays "running" forever. This class of bug is why a run can appear to
  vanish into nothing.

  Two independent mitigations, both included:
    1. The dashboard pings /health every ~15s while a run shows
       "running" (client-side keep-alive) — but only while that
       browser tab is open.
    2. THIS FILE also self-pings its own public URL every 4 minutes
       for the duration of the subprocess (server-side keep-alive) —
       works even if every browser tab is closed. Requires SELF_URL to
       be set (falls back to Render's auto-provided RENDER_EXTERNAL_URL
       if present).

  Neither is a substitute for a real job queue (Celery/RQ/Cloud Tasks/
  Render Background Worker) for production-grade reliability — this is
  an MVP-appropriate mitigation, documented as such.
"""

import collections
import os
import secrets
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import requests
from fastapi import FastAPI, BackgroundTasks, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import supabase_export


def _run_subprocess_with_capture(cmd, cwd, timeout_sec, tail_lines=50):
    """Runs cmd, streaming its output live (Render's Logs tab still shows
    real-time progress) while ALSO capturing the last N lines so a
    failure can self-report the actual reason into Supabase — the
    previous version just used subprocess.run(check=True), which on
    failure only ever produced 'exit status 1' with no indication why,
    forcing a manual trip into Render's logs every single time."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    tail = collections.deque(maxlen=tail_lines)

    def _reader():
        for line in proc.stdout:
            print(line, end="", flush=True)
            tail.append(line)

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()
    try:
        returncode = proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        reader_thread.join(timeout=5)
        raise RuntimeError(
            f"run.py timed out after {timeout_sec}s. Last output:\n" + "".join(tail))
    reader_thread.join(timeout=5)
    if returncode != 0:
        raise RuntimeError(
            f"run.py exited with code {returncode}. Last output:\n" + "".join(tail))

APP_DIR = Path(__file__).resolve().parent
RUN_API_KEY = os.environ.get("RUN_API_KEY", "")
SELF_URL = (os.environ.get("SELF_URL")
           or os.environ.get("RENDER_EXTERNAL_URL")
           or "").rstrip("/")
KEEPALIVE_INTERVAL_SEC = 4 * 60   # well under Render's ~15 min idle window

app = FastAPI(title="Helokn Lead Gen API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*\.lovable\.app|http://(localhost|127\.0\.0\.1):\d+",
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


def _keepalive_loop(stop_event: threading.Event):
    """Pings this service's own public URL periodically so the platform
    sees real inbound traffic and doesn't spin the container down while
    a long-running background pipeline job is still in flight."""
    if not SELF_URL:
        print("! SELF_URL/RENDER_EXTERNAL_URL not set — server-side "
              "keep-alive is disabled. Set SELF_URL to your public "
              "service URL to enable it.")
        return
    while not stop_event.wait(KEEPALIVE_INTERVAL_SEC):
        try:
            requests.get(f"{SELF_URL}/health", timeout=10)
        except requests.RequestException as e:
            print(f"! keep-alive ping failed (non-fatal): {e}")


def _run_pipeline(mode: str, triggered_by: str, run_id: str):
    sb = supabase_export.get_client()
    stop_keepalive = threading.Event()
    keepalive_thread = threading.Thread(
        target=_keepalive_loop, args=(stop_keepalive,), daemon=True)
    keepalive_thread.start()
    try:
        sb.table("pipeline_runs").upsert({
            "run_id": run_id, "status": "running",
            "triggered_by": triggered_by,
        }).execute()

        cmd = ["python", "-u", "run.py"] + (["--full"] if mode == "full" else [])
        # run.py now enforces its OWN wall-clock budget internally
        # (config.MAX_RUN_MINUTES, 25 min) and exports whatever's done
        # by then — this external timeout is a safety margin ABOVE that
        # guarantee (covers the Serper query phase before the internal
        # clock starts, plus export overhead), not a guess at how long
        # the pipeline might take. 40 min total ceiling.
        _run_subprocess_with_capture(cmd, cwd=APP_DIR, timeout_sec=60 * 40)

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
                "status": "failed", "error_message": str(e)[:4000],
                "finished_at": "now()",
            }).eq("run_id", run_id).execute()
        except Exception:
            pass
        raise
    finally:
        stop_keepalive.set()


@app.post("/run")
def run_pipeline(req: RunRequest, background_tasks: BackgroundTasks,
                 x_api_key: str | None = Header(default=None)):
    _check_key(x_api_key)
    if req.mode not in ("test", "full"):
        raise HTTPException(400, "mode must be 'test' or 'full'")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    background_tasks.add_task(_run_pipeline, req.mode, req.triggered_by, run_id)
    return {"status": "started", "mode": req.mode, "run_id": run_id}