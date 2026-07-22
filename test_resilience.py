"""
test_resilience.py — proves the two robustness fixes actually work:

  1. serper_client.SerperClient retries transient network failures
     instead of crashing immediately.
  2. run.py exports whatever leads were already validated even if the
     optional top-up phase crashes outright (the exact failure mode
     that lost 89 already-validated leads in production).

Run: python test_resilience.py   (0 network, 0 credits)
"""

import sys
import requests

import config
import serper_client
from serper_client import SerperClient, SerperRequestFailed


def test_retry_then_succeed():
    """First 2 calls raise a connection error, 3rd succeeds. Client
    should retry and return the successful result, not crash."""
    print("TEST 1: retry-then-succeed on transient network error")
    calls = {"n": 0}

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"organic": [{"title": "t", "link": "https://x.com", "snippet": "s"}]}

    def flaky_post(*a, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ConnectionError("simulated drop")
        return FakeResp()

    orig_post = requests.post
    orig_sleep = serper_client.time.sleep
    requests.post = flaky_post
    serper_client.time.sleep = lambda s: None  # skip real backoff delay
    config.SERPER_API_KEY = "fake-key-for-test"
    try:
        client = SerperClient(max_fresh_calls=5, results_per_query=10, max_retries=3)
        result = client.search("test query that will fail twice")
        assert calls["n"] == 3, f"expected 3 attempts, got {calls['n']}"
        assert result["organic"][0]["title"] == "t"
        assert client.fresh_calls == 1
        assert client.failed_queries == []
        print("  [ok] recovered after 2 failures, no crash, credit counted once\n")
    finally:
        requests.post = orig_post
        serper_client.time.sleep = orig_sleep


def test_permanent_failure_is_skippable():
    """All retries exhausted -> SerperRequestFailed, not a raw
    ConnectionError, so run.py's gather() can catch it cleanly."""
    print("TEST 2: permanent failure raises a catchable, specific exception")

    def always_fails(*a, **kw):
        raise requests.exceptions.ConnectionError("simulated permanent drop")

    orig_post = requests.post
    orig_sleep = serper_client.time.sleep
    requests.post = always_fails
    serper_client.time.sleep = lambda s: None
    config.SERPER_API_KEY = "fake-key-for-test"
    try:
        client = SerperClient(max_fresh_calls=5, results_per_query=10, max_retries=3)
        try:
            client.search("a query that always fails")
            assert False, "should have raised SerperRequestFailed"
        except SerperRequestFailed:
            pass
        assert client.failed_queries == ["a query that always fails"]
        print("  [ok] raises SerperRequestFailed after exhausting retries, "
              "tracked in failed_queries\n")
    finally:
        requests.post = orig_post
        serper_client.time.sleep = orig_sleep


def test_gather_skips_failed_query_and_continues():
    """run.py's gather() must not abort the whole loop when one query
    permanently fails — the other queries should still be processed."""
    print("TEST 3: gather() skips a permanently-failed query, keeps going")
    import run

    class FakeClient:
        def __init__(self):
            self.fresh_calls = 0
        def search(self, query):
            if query == "bad_query":
                raise SerperRequestFailed("simulated permanent failure")
            return {"organic": [{"title": "Our Certification Program",
                                 "link": f"https://{query}.org/certification",
                                 "snippet": "Our certification exam and candidate handbook."}]}

    query_list = [("good_query_1", "Certification Body"),
                 ("bad_query", "Certification Body"),
                 ("good_query_2", "Certification Body")]
    candidates, rejects = run.gather(FakeClient(), query_list)
    domains = {c.domain for c in candidates}
    assert "good_query_1.org" in domains and "good_query_2.org" in domains, \
        f"expected both good queries' results, got {domains}"
    print(f"  [ok] both good queries processed despite bad_query failing "
          f"({len(candidates)} candidates kept)\n")


def test_export_survives_topup_crash():
    """The critical fix: if the top-up phase throws ANY exception, the
    leads already validated in the primary pass must still be exported,
    not lost."""
    print("TEST 4: export happens even if top-up phase crashes outright")
    import csv
    import run
    import serper_client as sc

    # Fixed set of good results for the primary pass.
    GOOD_ITEMS = [
        {"title": "Our Certification Program", "link": f"https://good{i}.org/certification",
         "snippet": "Our certification exam and candidate handbook. Recertification "
                    "requires continuing education credits."}
        for i in range(3)
    ]

    def fake_search(self, query):
        self.fresh_calls += 1
        if query == "TOPUP_TRIGGER":
            # This is what a crash mid-topup looks like: an exception
            # escaping the network layer entirely.
            raise RuntimeError("simulated catastrophic failure during topup")
        return {"organic": GOOD_ITEMS}

    def fake_fetch(url, timeout=None):
        return ("<html><head><title>Good Org</title>"
                '<meta property="og:site_name" content="Good Org"></head>'
                "<body>Our certification exam and candidate handbook. "
                "Recertification requires continuing education credits.</body></html>")

    import companyclean, enrich
    orig_search = sc.SerperClient.search
    orig_cc_fetch = companyclean.fetch_page
    orig_en_fetch = enrich.fetch_page
    orig_bank = run.QUERY_BANK
    orig_prod_count = run.PRODUCTION_QUERY_COUNT

    sc.SerperClient.search = fake_search
    companyclean.fetch_page = fake_fetch
    enrich.fetch_page = fake_fetch
    # 1 primary query (good, produces 3 leads) + 1 top-up query that crashes
    run.QUERY_BANK = [("PRIMARY", "Certification Body"),
                      ("TOPUP_TRIGGER", "Certification Body")]
    run.PRODUCTION_QUERY_COUNT = 1  # forces the crash query into top-up

    try:
        sys.argv = ["run.py", "--full", "--max-queries", "5", "--target", "100"]
        run.main()  # must NOT raise, despite the simulated crash

        leads = list(csv.DictReader(open(config.OUTPUT_DIR / "helokn_leads.csv")))
        assert len(leads) == 3, f"expected the 3 primary-pass leads to survive, got {len(leads)}"
        print(f"  [ok] {len(leads)} primary-pass leads exported despite top-up "
              f"phase crashing with RuntimeError\n")
    finally:
        sc.SerperClient.search = orig_search
        companyclean.fetch_page = orig_cc_fetch
        enrich.fetch_page = orig_en_fetch
        run.QUERY_BANK = orig_bank
        run.PRODUCTION_QUERY_COUNT = orig_prod_count


def test_deadline_enforcement_cuts_off_slow_work():
    """Proves a wall-clock deadline (as used by config.MAX_RUN_MINUTES)
    actually cuts off slow work instead of blocking regardless: with a
    tiny test deadline and one candidate that 'hangs' far longer than it,
    the same ThreadPoolExecutor + as_completed(timeout=...) +
    shutdown(wait=False) pattern process() uses must return promptly
    with the fast candidates done and the slow one dropped."""
    print("TEST 5: deadline pattern actually cuts off slow work "
          "(the mechanism behind config.MAX_RUN_MINUTES)")
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FTE

    def fake_process_one(domain):
        if domain == "slow.org":
            time.sleep(5)  # far longer than the test's 0.5s deadline
        return domain

    domains = ["fast1.org", "fast2.org", "slow.org"]
    deadline = time.monotonic() + 0.5
    pool = ThreadPoolExecutor(max_workers=10)
    start = time.monotonic()
    futures = {pool.submit(fake_process_one, d): d for d in domains}
    done_domains = []
    try:
        for fut in as_completed(futures, timeout=max(0, deadline - time.monotonic())):
            done_domains.append(fut.result())
    except FTE:
        pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, \
        f"blocked for {elapsed:.1f}s — deadline did not cut off the slow thread"
    assert "fast1.org" in done_domains and "fast2.org" in done_domains, \
        f"fast candidates should have completed: {done_domains}"
    assert "slow.org" not in done_domains, \
        f"slow candidate should have been cut off, not waited for: {done_domains}"
    print(f"  [ok] returned in {elapsed:.2f}s (not ~5s) — fast candidates "
          f"completed {done_domains}, slow one correctly dropped\n")


def test_collect_process_results_handles_none_entries():
    """Direct regression test for a bug that reached production: a
    results list with real (kind, payload) tuples MIXED WITH bare None
    entries (candidates that never completed before a deadline) crashed
    with 'TypeError: cannot unpack non-iterable NoneType object' — the
    None-check was happening AFTER attempting to unpack, not before.
    This tests the actual extracted function, not just the surrounding
    threading pattern (which is what the previous deadline test did,
    and why this bug weren't caught the first time)."""
    print("TEST 6: collect_process_results survives a mix of real "
          "results and None (incomplete-before-deadline) entries")
    import run

    results = [
        ("lead", {"company_name": "Fast Co", "domain": "fast.org"}),
        None,  # never completed before the deadline — the crash trigger
        ("leads", [{"company_name": "School A", "domain": "school.org"},
                  {"company_name": "School A", "domain": "school.org"}]),
        None,
        ("reject", {"domain": "bad.org", "rejection_reason": "no_trigger"}),
    ]

    leads, rejects = run.collect_process_results(results)  # must not raise

    assert len(leads) == 3, f"expected 1 + 2 leads, got {len(leads)}"
    assert leads[0]["domain"] == "fast.org"
    assert {l["domain"] for l in leads[1:]} == {"school.org"}
    assert len(rejects) == 1 and rejects[0]["domain"] == "bad.org"
    print(f"  [ok] {len(leads)} leads + {len(rejects)} rejects collected "
          f"correctly, None entries silently skipped, no crash\n")


if __name__ == "__main__":
    import shutil
    shutil.rmtree(config.CACHE_DIR, ignore_errors=True)
    shutil.rmtree(config.OUTPUT_DIR, ignore_errors=True)

    test_retry_then_succeed()
    test_permanent_failure_is_skippable()
    test_gather_skips_failed_query_and_continues()
    test_export_survives_topup_crash()
    test_deadline_enforcement_cuts_off_slow_work()
    test_collect_process_results_handles_none_entries()
    print("ALL RESILIENCE TESTS PASSED")