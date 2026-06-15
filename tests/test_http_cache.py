"""F4 tests — HTTP retry + stale-cache fallback (hermetic, monkeypatched httpx)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from worldcupagents.dataflows import http_cache as hc


def _cache(tmp_path) -> hc.HTTPCache:
    # min_interval/backoff zeroed so tests never sleep.
    return hc.HTTPCache(str(tmp_path / "cache"), min_interval=0, backoff=0)


def _ok_response(payload):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)


def test_retry_recovers_from_transient_resets(tmp_path, monkeypatch):
    calls = {"n": 0}

    def flaky_get(url, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionResetError(54, "Connection reset by peer")
        return _ok_response({"ok": True})

    monkeypatch.setattr(hc.httpx, "get", flaky_get)
    assert _cache(tmp_path).get_json("https://x/api") == {"ok": True}
    assert calls["n"] == 3                                  # failed twice, third succeeded


def test_stale_cache_served_when_all_attempts_fail(tmp_path, monkeypatch):
    cache = _cache(tmp_path)
    # Seed the cache, then expire it by requesting with ttl=0.
    monkeypatch.setattr(hc.httpx, "get", lambda url, headers=None, timeout=None: _ok_response({"v": 1}))
    assert cache.get_json("https://x/api") == {"v": 1}

    def always_reset(url, headers=None, timeout=None):
        raise ConnectionResetError(54, "Connection reset by peer")

    monkeypatch.setattr(hc.httpx, "get", always_reset)
    assert cache.get_json("https://x/api", ttl=0) == {"v": 1}   # stale copy beats nothing


def test_raises_when_no_cache_and_all_attempts_fail(tmp_path, monkeypatch):
    def always_reset(url, headers=None, timeout=None):
        raise ConnectionResetError(54, "Connection reset by peer")

    monkeypatch.setattr(hc.httpx, "get", always_reset)
    with pytest.raises(ConnectionResetError):
        _cache(tmp_path).get_json("https://x/never-cached")


def test_4xx_is_not_retried(tmp_path, monkeypatch):
    """403 is deterministic — one attempt, no retry storm (the Wolves bug)."""
    import httpx as real_httpx
    calls = {"n": 0}

    def forbidden(url, headers=None, timeout=None):
        calls["n"] += 1
        req = real_httpx.Request("GET", url)
        resp = real_httpx.Response(403, request=req)
        raise real_httpx.HTTPStatusError("403", request=req, response=resp)

    monkeypatch.setattr(hc.httpx, "get", forbidden)
    with pytest.raises(real_httpx.HTTPStatusError):
        _cache(tmp_path).get_json("https://x/restricted")
    assert calls["n"] == 1                                  # NOT 3


def test_429_is_still_retried(tmp_path, monkeypatch):
    import httpx as real_httpx
    calls = {"n": 0}

    def rate_limited_then_ok(url, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            req = real_httpx.Request("GET", url)
            raise real_httpx.HTTPStatusError("429", request=req,
                                             response=real_httpx.Response(429, request=req))
        return _ok_response({"ok": 1})

    monkeypatch.setattr(hc.httpx, "get", rate_limited_then_ok)
    assert _cache(tmp_path).get_json("https://x/limited") == {"ok": 1}
    assert calls["n"] == 2


def test_fresh_cache_short_circuits_network(tmp_path, monkeypatch):
    cache = _cache(tmp_path)
    path = cache._path("https://x/api")
    path.write_text(json.dumps({"cached": True}), encoding="utf-8")

    def boom(url, headers=None, timeout=None):
        raise AssertionError("network should not be touched on a fresh cache hit")

    monkeypatch.setattr(hc.httpx, "get", boom)
    assert cache.get_json("https://x/api") == {"cached": True}
