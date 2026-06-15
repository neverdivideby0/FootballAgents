"""Read-through disk cache + polite rate-limiting + retry for vendor HTTP calls.

football-data.org's free tier allows ~10 requests/minute, so we (a) cache every
JSON response to disk keyed by URL and (b) throttle live calls to a minimum
interval. Cached hits cost zero requests — reruns of the same fixture are free.

Resilience: transient failures (connection resets, server hiccups) are retried
with a short backoff, and if all attempts fail we serve a STALE cached copy
(ignoring TTL) rather than failing — yesterday's squad list beats no squad list.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class HTTPCache:
    def __init__(self, cache_dir: str, min_interval: float = 6.5, timeout: float = 20.0,
                 retries: int = 3, backoff: float = 0.5):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval  # seconds between live calls (~9/min)
        self.timeout = timeout
        self.retries = retries            # attempts per URL before stale-cache fallback
        self.backoff = backoff            # base sleep between attempts (×attempt)
        self._last_call = 0.0

    def _path(self, url: str) -> Path:
        return self.dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")

    def get_json(self, url: str, headers: dict | None = None, ttl: int = 86400) -> Any:
        path = self._path(url)
        if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
            return json.loads(path.read_text(encoding="utf-8"))

        last_err: Exception | None = None
        for attempt in range(1, self.retries + 1):
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = httpx.get(url, headers=headers or {}, timeout=self.timeout)
                self._last_call = time.time()
                resp.raise_for_status()
                data = resp.json()
                path.write_text(json.dumps(data), encoding="utf-8")
                return data
            except Exception as e:  # noqa: BLE001 — resets/timeouts/5xx: retry, then stale
                self._last_call = time.time()
                last_err = e
                if _is_permanent(e):
                    break  # 403/404/...: deterministic — retrying just burns quota
                if attempt < self.retries:
                    logger.warning("HTTP attempt %d/%d failed for %s (%s); retrying",
                                   attempt, self.retries, url, e)
                    time.sleep(self.backoff * attempt)

        if path.exists():  # attempts exhausted — serve the stale copy
            logger.warning("HTTP failed (%s); serving STALE cache for %s", last_err, url)
            return json.loads(path.read_text(encoding="utf-8"))
        raise last_err  # no cache to fall back on — let the provider degrade


def _is_permanent(e: Exception) -> bool:
    """Client errors (4xx except 429) are deterministic — never retry them."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        return 400 <= code < 500 and code != 429
    return False
