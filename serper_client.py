"""
serper_client.py — the ONLY module allowed to spend Serper credits.

Cost controls:
  * Disk cache keyed by (query, num). A repeated query costs 0 credits,
    across runs, forever, until you delete cache/serper/.
  * Hard budget: the client refuses to exceed max_fresh_calls and raises
    BudgetExhausted instead of silently spending.
  * Counters: fresh_calls (credits spent) vs cache_hits (credits saved),
    printed in the run summary.

Resilience: transient network errors (dropped connections, timeouts) are
retried with backoff instead of crashing the run — a flaky connection on
query 90 of 90 should not lose the other 89 already-processed leads.
"""

import hashlib
import json
import time

import requests

import config


class BudgetExhausted(Exception):
    """Raised when a fresh Serper call would exceed the budget."""


class SerperRequestFailed(Exception):
    """Raised when a fresh Serper call fails after all retries."""


NETWORK_RETRY_STATUS = {429, 500, 502, 503, 504}


class SerperClient:
    def __init__(self, max_fresh_calls: int, results_per_query: int,
                max_retries: int = 3):
        self.max_fresh_calls = max_fresh_calls
        if results_per_query > 10:
            print(f"  ! --max-results {results_per_query} > 10: many Serper "
                  f"plans (including free tier) reject num>10 with a "
                  f"misleading 'query pattern not allowed' 400 error. "
                  f"Clamping to 10. Raise this only after confirming your "
                  f"plan supports it (see diagnose3.py).")
            results_per_query = 10
        self.results_per_query = results_per_query
        self.max_retries = max_retries
        self.fresh_calls = 0
        self.cache_hits = 0
        self.failed_queries = []
        self.cache_dir = config.CACHE_DIR / "serper"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- cache ---------------------------------------------------------
    def _cache_path(self, query: str):
        key = hashlib.sha256(
            f"{query}|num={self.results_per_query}".encode()
        ).hexdigest()[:24]
        return self.cache_dir / f"{key}.json"

    # -- public --------------------------------------------------------
    def search(self, query: str) -> dict:
        """Return Serper response for query, from cache when possible.

        Retries transient network failures (connection drops, timeouts,
        429/5xx) up to max_retries times with exponential backoff before
        giving up and raising SerperRequestFailed. A single flaky request
        no longer needs to crash the whole pipeline — callers (run.py's
        gather()) catch SerperRequestFailed and skip to the next query.
        """
        path = self._cache_path(query)
        if path.exists():
            self.cache_hits += 1
            return json.loads(path.read_text())

        if self.fresh_calls >= self.max_fresh_calls:
            raise BudgetExhausted(
                f"Serper budget of {self.max_fresh_calls} fresh calls reached."
            )
        if not config.SERPER_API_KEY:
            raise RuntimeError(
                "SERPER_API_KEY is not set. Create a .env file "
                "(see .env.example) before running."
            )

        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    config.SERPER_ENDPOINT,
                    headers={
                        "X-API-KEY": config.SERPER_API_KEY,
                        "Content-Type": "application/json",
                    },
                    json={"q": query, "num": self.results_per_query},
                    timeout=config.HTTP_TIMEOUT,
                )
                if resp.status_code in NETWORK_RETRY_STATUS:
                    raise requests.exceptions.HTTPError(
                        f"{resp.status_code} (retryable)", response=resp)
                resp.raise_for_status()
                data = resp.json()
                self.fresh_calls += 1
                path.write_text(json.dumps(data, indent=1))
                time.sleep(0.4)  # be polite; avoids burst rate limits
                return data
            except requests.exceptions.RequestException as e:
                last_err = e
                if attempt < self.max_retries:
                    wait = 2 ** attempt  # 2s, 4s, 8s, ...
                    print(f"  ! Serper request failed (attempt {attempt}/"
                          f"{self.max_retries}): {e}. Retrying in {wait}s...")
                    time.sleep(wait)

        self.failed_queries.append(query)
        raise SerperRequestFailed(
            f"Query failed after {self.max_retries} attempts: {query!r} "
            f"({last_err})")

    def remaining_budget(self) -> int:
        return self.max_fresh_calls - self.fresh_calls