# Brief for Google Antigravity

Paste this into a new Antigravity mission after opening this project folder.

## Context
This is a working Python lead-generation pipeline (`run.py` + supporting
modules) that searches for organizations that might buy Helokn (an AI
evaluation platform), validates them, scores them, and exports CSVs. It
already works correctly when run locally with `python run.py --full`.

I've added three new files to make it cloud-deployable:
- `supabase_export.py` — pushes CSV output into Supabase tables
- `api.py` — a FastAPI wrapper so a web dashboard can trigger a run
- `Dockerfile` — container definition for Cloud Run
- `supabase_schema.sql` — the Supabase schema (I've already run this in
  Supabase's SQL editor — the tables exist)

## What I need you to do
1. Review `api.py` and `supabase_export.py` — they're first drafts, not
   hardened. Fix anything wrong, and specifically:
   - The `/run` endpoint currently runs the pipeline synchronously in a
     background task inside the same request/container. A `--full` run
     can take 10-20 minutes. Confirm this is safe on Cloud Run (request
     timeout, container lifecycle) or change the architecture to a Cloud
     Run Job / Pub-Sub triggered job if it isn't.
   - Add basic auth/API-key protection to `/run` so it isn't a public
     unauthenticated way to burn my Serper credits.
   - Make sure `SERPER_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
     are read from Cloud Run environment variables / Secret Manager, not
     baked into the image.
2. Test locally first: build the Docker image, run it, hit `/health` and
   `/run` with mode "test" (1 query, 1 lead — cheap), confirm a row
   lands in the Supabase `leads` table.
3. Deploy to Google Cloud Run using your Cloud Run deploy flow. Use a
   region close to me, minimum instances 0 (scale to zero — this doesn't
   need to be always-on), memory 512Mi is probably enough.
4. Give me back: the deployed Cloud Run URL, and confirm `/health`
   responds on it.

## What NOT to change
Don't modify the pipeline logic itself (junkfilter.py, companyclean.py,
scorer.py, enrich.py, queries.py) — that's been tuned through several
rounds of quality auditing and works as intended. If you think something
there is a bug, flag it to me rather than silently fixing it.