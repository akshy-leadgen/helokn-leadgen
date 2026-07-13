"""
run.py — Helokn lead generation pipeline (fresh MVP).

    python run.py            safe TEST mode: 1 query, 1 lead (default)
    python run.py --full     PRODUCTION mode: full batch (explicit only)

Pipeline (mentor's flow, with one improvement — company validation
happens BEFORE contact enrichment, so junk never gets enriched):

  ICP query bank
    -> Gather        (Serper, cached, budget-capped)
    -> Junk filter   (free string rules: blogs/vendors/jobs/articles out)
    -> Dedupe        (one candidate per domain, best trigger kept)
    -> Validate org  (free homepage fetch: real name, real buyer, alive)
    -> Enrich contact(free contact-page scrape, one best contact)
    -> Score & tier  (honest rubric, audit trail)
    -> Export CSVs   (helokn_leads.csv + rejected_candidates.csv)
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import config
from queries import QUERY_BANK, PRODUCTION_QUERY_COUNT
from serper_client import SerperClient, BudgetExhausted, SerperRequestFailed
from junkfilter import filter_result
from companyclean import resolve_company
from enrich import enrich_contact
from scorer import score_lead
from exporter import write_leads, write_rejects, INDUSTRY_BY_ICP


def parse_args():
    p = argparse.ArgumentParser(description="Helokn lead generation pipeline")
    p.add_argument("--full", action="store_true",
                   help="PRODUCTION mode: run the full batch. Costs Serper credits.")
    p.add_argument("--max-queries", type=int, default=config.MAX_SERPER_QUERIES,
                   help=f"max fresh Serper calls (default {config.MAX_SERPER_QUERIES})")
    p.add_argument("--max-results", type=int, default=config.MAX_RESULTS_PER_QUERY,
                   help=f"results per query (default {config.MAX_RESULTS_PER_QUERY})")
    p.add_argument("--max-contacts", type=int, default=config.MAX_CONTACTS_PER_COMPANY,
                   help="contacts per company (default 1)")
    p.add_argument("--target", type=int, default=config.TARGET_LEADS,
                   help=f"qualified-lead target (default {config.TARGET_LEADS})")
    return p.parse_args()


def gather(client, query_list):
    """Stage 1+2: search (cached) and junk-filter every organic result.

    A single query that fails all its retries (SerperRequestFailed) is
    logged and skipped — it does NOT abort the run and lose everything
    already gathered. Only a hit budget (BudgetExhausted) stops gathering
    entirely, since that's a deliberate limit, not a failure.
    """
    candidates, rejects = [], []
    for query, icp_hint in query_list:
        try:
            data = client.search(query)
        except BudgetExhausted:
            print(f"  ! Serper budget reached — stopping gathering.")
            break
        except SerperRequestFailed as e:
            print(f"  ! Skipping query after repeated failures: {e}")
            continue
        for item in data.get("organic", []):
            cand, rej = filter_result(item, query, icp_hint)
            if cand:
                candidates.append(cand)
            else:
                rejects.append(rej)
    return candidates, rejects


def dedupe(candidates):
    """One candidate per domain; keep the one with the most triggers /
    strongest evidence. Runs BEFORE any enrichment, per credit rules."""
    best = {}
    for c in candidates:
        cur = best.get(c.domain)
        if cur is None or (len(c.triggers), not c.evidence_weak) > \
                          (len(cur.triggers), not cur.evidence_weak):
            best[c.domain] = c
    return list(best.values())


def build_lead(cand, info, contact, s):
    return {
        "company_name": info["company_name"],
        "domain": cand.domain,
        "website_url": info["website_url"],
        "industry": INDUSTRY_BY_ICP.get(s["icp_category"], "Other"),
        "icp_category": s["icp_category"],
        "lead_status": "Qualified" if s["tier"] in ("A", "B") else "Needs Review",
        "score": s["score"],
        "tier": s["tier"],
        "trigger_type": s["trigger_type"],
        "evidence_summary": (cand.snippet or cand.raw_title)[:300],
        "evidence_url": cand.raw_url,
        "contact_status": contact["contact_status"],
        "contact_name": contact["contact_name"],
        "contact_title": contact["contact_title"],
        "contact_email": contact["contact_email"],
        "email_confidence": contact["email_confidence"],
        "contact_source_url": contact["contact_source_url"],
        "linkedin_search_url": contact["linkedin_search_url"],
        "google_search_url": contact["google_search_url"],
        "serper_source_query": cand.source_query,
        "source_result_url": cand.raw_url,
        "audit_trail": s["audit_trail"],
        "rejection_reason": "",
        "notes": contact.get("notes_hint", ""),
    }


def main():
    args = parse_args()
    production = args.full

    print("=" * 64)
    if production:
        print("MODE: PRODUCTION  (explicit --full)  — spends Serper credits")
        query_list = QUERY_BANK  # capped by budget below
        primary = min(PRODUCTION_QUERY_COUNT, args.max_queries)
    else:
        print("MODE: TEST (default) — 1 query, 1 lead. Use --full for production.")
        query_list = QUERY_BANK[: config.TEST_MODE_QUERIES]
        primary = config.TEST_MODE_QUERIES
    print("=" * 64)

    client = SerperClient(max_fresh_calls=args.max_queries,
                          results_per_query=args.max_results)

    # ---- Stage 1+2: gather + junk filter (primary query set) ----
    print(f"\n[1/5] Gathering: up to {primary} queries × {args.max_results} results")
    candidates, rejects = gather(client, query_list[:primary])
    print(f"      raw kept: {len(candidates)}  junk rejected: {len(rejects)}")

    # ---- Stage 3: dedupe domains BEFORE any enrichment ----
    candidates = dedupe(candidates)
    print(f"[2/5] Deduped to {len(candidates)} unique domains")

    if not production:
        candidates = candidates[: config.TEST_MODE_LEADS]
        print(f"      test mode: processing only {len(candidates)} lead(s)")

    # ---- Stage 4+5+6: validate org -> enrich -> score ----
    # Parallelized: each candidate's work is ~all network wait (fetching
    # other people's websites), so a thread pool gives near-linear
    # speedup. Results are collected per-candidate and merged in input
    # order afterward so output stays deterministic.
    def process_one(cand):
        """Returns ('lead', dict) | ('reject', dict)."""
        try:
            info = resolve_company(cand)
            if not info["valid"]:
                return ("reject", {
                    "raw_title": cand.raw_title, "raw_url": cand.raw_url,
                    "domain": cand.domain, "source_query": cand.source_query,
                    "rejection_reason": info["rejection_reason"],
                    "explanation": info["explanation"]})
            contact = enrich_contact(cand.domain, info["company_name"],
                                     args.max_contacts)
            s = score_lead(cand, info, contact)
            if s["tier"] == "Rejected":
                return ("reject", {
                    "raw_title": cand.raw_title, "raw_url": cand.raw_url,
                    "domain": cand.domain, "source_query": cand.source_query,
                    "rejection_reason": "failed_scoring",
                    "explanation": f"Score {s['score']} < 50. {s['audit_trail']}"})
            lead = build_lead(cand, info, contact, s)
            if info.get("name_is_domain_guess"):
                note = "Company name derived from domain — verify before outreach."
                lead["notes"] = (lead["notes"] + " " + note).strip()
            return ("lead", lead)
        except Exception as e:
            # One malformed page/site should not lose progress on the
            # other candidates in flight.
            return ("reject", {
                "raw_title": cand.raw_title, "raw_url": cand.raw_url,
                "domain": cand.domain, "source_query": cand.source_query,
                "rejection_reason": "processing_error",
                "explanation": f"{type(e).__name__}: {e}"})

    def process(cands):
        leads = []
        if not cands:
            return leads
        done = 0
        with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
            futures = {pool.submit(process_one, c): idx
                       for idx, c in enumerate(cands)}
            results = [None] * len(cands)
            for fut in as_completed(futures):
                idx = futures[fut]
                results[idx] = fut.result()
                done += 1
                kind = results[idx][0]
                print(f"[3/5] ({done}/{len(cands)}) {cands[idx].domain} "
                      f"-> {kind}")
        for kind, payload in results:
            if kind == "lead":
                leads.append(payload)
            else:
                rejects.append(payload)
        return leads
        return leads

    leads = process(candidates)

    # ---- Adaptive top-up: only in production, only within budget ----
    # Wrapped defensively: this is a "nice to have" pass to close the gap
    # to --target. If ANYTHING in here fails (network drop, unexpected
    # error), we must still export the leads already validated above —
    # losing 89 good leads to a crash in an optional bonus step is a
    # correctness bug, not acceptable behavior.
    if production and len(leads) < args.target and client.remaining_budget() > 0 \
            and len(QUERY_BANK) > primary:
        print(f"\n[3.5/5] {len(leads)} < target {args.target}: "
              f"topping up from query bank ({client.remaining_budget()} calls left)")
        try:
            extra_c, extra_r = gather(client, QUERY_BANK[primary:])
            rejects.extend(extra_r)
            seen = {l["domain"] for l in leads}
            extra_c = [c for c in dedupe(extra_c) if c.domain not in seen]
            leads.extend(process(extra_c))
        except Exception as e:
            print(f"  ! Top-up step failed ({type(e).__name__}: {e}). "
                  f"Exporting the {len(leads)} leads already gathered instead "
                  f"of losing them. Re-run --full afterward (cache means "
                  f"already-fetched queries/pages cost nothing extra).")

    # ---- Export (always runs, regardless of what happened above) ----
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M")
    lead_path = write_leads(leads, run_stamp=run_stamp)
    rej_path = write_rejects(rejects, run_stamp=run_stamp)

    # ---- Summary ----
    tiers = {t: sum(1 for l in leads if l["tier"] == t) for t in "ABC"}
    print("\n" + "=" * 64)
    print("RUN SUMMARY")
    print(f"  Serper fresh calls (credits spent) : {client.fresh_calls}")
    print(f"  Serper cache hits (credits saved)  : {client.cache_hits}")
    print(f"  Junk / rejected candidates         : {len(rejects)}")
    print(f"  Qualified leads exported           : {len(leads)}"
          f"   (A: {tiers['A']}  B: {tiers['B']}  C: {tiers['C']})")
    print(f"  Leads CSV   : {lead_path}")
    print(f"  Rejects CSV : {rej_path}")
    if client.failed_queries:
        print(f"  Queries that failed after retries ({len(client.failed_queries)}): "
              f"these were skipped, not charged. Re-run --full to retry them "
              f"(cached queries won't be re-spent):")
        for q in client.failed_queries:
            print(f"    - {q}")
    if production and len(leads) < args.target:
        print(f"  NOTE: under target of {args.target}. Options: raise "
              f"--max-queries (bank has {len(QUERY_BANK)} queries), add "
              f"queries to queries.py, or loosen nothing — quality first.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())