"""InventoryCache unit tests — fake clock, no vCenter."""

from __future__ import annotations

import threading
import time

import pytest

from app.inventory_cache import InventoryCache, InventoryUnavailable


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Loader:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        item = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(item, Exception):
            raise item
        return item


def _get(cache, loader, **kw):
    opts = {"ttl": 60, "stale_ttl": 600, "error_backoff": 10}
    opts.update(kw)
    return cache.get("k", loader, **opts)


def test_fresh_hit_skips_loader():
    clock = _Clock()
    cache = InventoryCache(clock)
    loader = _Loader(["a"])
    assert _get(cache, loader).source == "live"
    clock.now += 30
    res = _get(cache, loader)
    assert (res.value, res.source, res.age) == (["a"], "cache", 30)
    assert loader.calls == 1


def test_expired_entry_reloads():
    clock = _Clock()
    cache = InventoryCache(clock)
    loader = _Loader(["a"], ["b"])
    _get(cache, loader)
    clock.now += 61
    res = _get(cache, loader)
    assert (res.value, res.source) == (["b"], "live")
    assert loader.calls == 2


def test_refresh_bypasses_fresh_entry():
    clock = _Clock()
    cache = InventoryCache(clock)
    loader = _Loader(["a"], ["b"])
    _get(cache, loader)
    res = _get(cache, loader, refresh=True)
    assert (res.value, res.source) == (["b"], "live")


def test_ttl_zero_always_loads():
    cache = InventoryCache(_Clock())
    loader = _Loader(["a"])
    _get(cache, loader, ttl=0)
    _get(cache, loader, ttl=0)
    assert loader.calls == 2


def test_failure_serves_stale_value():
    clock = _Clock()
    cache = InventoryCache(clock)
    loader = _Loader(["a"], RuntimeError("vCenter down"))
    _get(cache, loader)
    clock.now += 120
    res = _get(cache, loader)
    assert (res.value, res.source, res.age) == (["a"], "stale", 120)


def test_stale_value_expires():
    clock = _Clock()
    cache = InventoryCache(clock)
    loader = _Loader(["a"], RuntimeError("vCenter down"))
    _get(cache, loader)
    clock.now += 601
    with pytest.raises(InventoryUnavailable, match="vCenter down"):
        _get(cache, loader)


def test_stale_ttl_zero_never_serves_stale():
    clock = _Clock()
    cache = InventoryCache(clock)
    loader = _Loader(["a"], RuntimeError("vCenter down"))
    _get(cache, loader, stale_ttl=0)
    clock.now += 61
    with pytest.raises(InventoryUnavailable):
        _get(cache, loader, stale_ttl=0)


def test_failure_without_cache_raises_with_retry_after():
    cache = InventoryCache(_Clock())
    with pytest.raises(InventoryUnavailable) as info:
        _get(cache, _Loader(RuntimeError("timeout")))
    assert str(info.value) == "timeout"
    assert info.value.retry_after == 10


def test_error_backoff_suppresses_reload_then_retries():
    clock = _Clock()
    cache = InventoryCache(clock)
    loader = _Loader(RuntimeError("down"), ["a"])
    with pytest.raises(InventoryUnavailable):
        _get(cache, loader)
    clock.now += 4
    with pytest.raises(InventoryUnavailable) as info:
        _get(cache, loader, refresh=True)
    assert info.value.retry_after == pytest.approx(6)
    assert loader.calls == 1
    clock.now += 7
    assert _get(cache, loader).value == ["a"]
    assert loader.calls == 2


def test_backoff_serves_stale_without_calling_loader():
    clock = _Clock()
    cache = InventoryCache(clock)
    loader = _Loader(["a"], RuntimeError("down"))
    _get(cache, loader)
    clock.now += 61
    assert _get(cache, loader).source == "stale"
    clock.now += 1
    assert _get(cache, loader).source == "stale"
    assert loader.calls == 2


def test_keys_are_independent_and_clear_drops_all():
    cache = InventoryCache(_Clock())
    a, b = _Loader(["a"]), _Loader(["b"])
    opts = {"ttl": 60, "stale_ttl": 600, "error_backoff": 10}
    assert cache.get("a", a, **opts).value == ["a"]
    assert cache.get("b", b, **opts).value == ["b"]
    cache.clear()
    cache.get("a", a, **opts)
    assert a.calls == 2


def test_concurrent_callers_share_one_load():
    cache = InventoryCache()
    gate = threading.Event()
    calls = []

    def slow():
        calls.append(1)
        gate.wait(5)
        return ["a"]

    results = []

    def worker():
        results.append(cache.get("k", slow, ttl=60, stale_ttl=600, error_backoff=10))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.1)
    gate.set()
    for t in threads:
        t.join(5)
    assert len(calls) == 1
    assert sorted(r.source for r in results) == ["cache"] * 4 + ["live"]
