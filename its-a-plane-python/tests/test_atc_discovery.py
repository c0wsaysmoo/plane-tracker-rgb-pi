"""ATC feed-discovery must never probe the network under self._lock.

The auto-tuner used to run a ~15-URL probe sweep inside self._lock, stalling
status()/the mirror poll for up to ~20s on a cache-cold airport. Discovery now
runs in a background worker; _feeds_for_airport only reads the cache or enqueues.
These tests use a mocked probe (no network)."""
import threading
import time
from unittest.mock import patch

from utilities.atc_audio import ATCAudioManager


def _bare_manager():
    m = ATCAudioManager.__new__(ATCAudioManager)
    m._seed = {"KJFK": {"feeds": {"twr": "kjfk_twr"}, "lat": 40.6, "lon": -73.8}}
    m._discovered = {}
    m._discover_queue = set()
    m._discover_lock = threading.Lock()
    m._discover_thread = None
    m._probe_cooldown_until = 0.0
    m._lock = threading.RLock()
    return m


def test_seed_airport_never_probes():
    m = _bare_manager()
    with patch.object(m, "_probe_feed", side_effect=AssertionError("probed!")):
        assert m._feeds_for_airport("KJFK") == {"twr": "kjfk_twr"}


def test_cache_miss_does_not_probe_synchronously_and_worker_discovers():
    m = _bare_manager()
    calls = []

    def fake_probe(code, timeout=2.0):
        calls.append(code)
        return code == "kxyz_twr"

    with patch.object(m, "_probe_feed", side_effect=fake_probe), \
            patch("utilities.atc_audio._atomic_write", lambda *a, **k: None):
        # Cache miss returns {} immediately with NO synchronous probe.
        assert m._feeds_for_airport("KXYZ") == {}
        assert calls == []

        # Background worker discovers off-thread.
        for _ in range(50):
            if m._discovered.get("KXYZ") is not None:
                break
            time.sleep(0.1)
        assert m._discovered.get("KXYZ", {}).get("feeds") == {"twr": "kxyz_twr"}
        assert calls, "worker should have probed"

        # Subsequent read is a cache hit — no new probe.
        n = len(calls)
        assert m._feeds_for_airport("KXYZ") == {"twr": "kxyz_twr"}
        assert len(calls) == n


def test_cooldown_blocks_enqueue():
    m = _bare_manager()
    m._probe_cooldown_until = time.time() + 3600   # in cooldown
    with patch.object(m, "_probe_feed", side_effect=AssertionError("probed!")):
        assert m._feeds_for_airport("KXYZ") == {}
        assert "KXYZ" not in m._discover_queue   # not queued during cooldown


def test_incomplete_sweep_not_cached():
    """A 403 ban tripped mid-sweep is incomplete AND the worker must not persist
    it (a later un-banned sweep completes it)."""
    m = _bare_manager()

    def fake_probe(code, timeout=2.0):
        # Simulate a 403: real _probe_feed sets the global cooldown then returns
        # None. That — not a plain timeout — is what makes a sweep incomplete.
        m._probe_cooldown_until = time.time() + 3600
        return None

    with patch.object(m, "_probe_feed", side_effect=fake_probe), \
            patch("utilities.atc_audio._atomic_write", lambda *a, **k: None):
        feeds, complete = m._probe_airport_feeds("KABC")
        assert complete is False

        # The worker must NOT cache an incomplete sweep. Drive it once: reset the
        # cooldown so it probes, the first probe re-trips it → incomplete.
        m._probe_cooldown_until = 0.0
        m._discover_queue = {"KABC"}
        with patch("time.sleep", lambda *_: None):
            m._discover_worker()
        assert "KABC" not in m._discovered   # nothing persisted


def test_benign_timeout_keeps_found_feed():
    """A per-mount timeout/5xx (None, but NO cooldown) must not abort the sweep
    or discard a live feed already found — else the airport re-enqueues every
    tick during transient flakiness (the regression the off-lock move introduced)."""
    m = _bare_manager()

    def fake_probe(code, timeout=2.0):
        if code == "kabc_twr":
            return True     # tower feed is alive
        return None         # every other mount times out — benign, no cooldown

    with patch.object(m, "_probe_feed", side_effect=fake_probe):
        feeds, complete = m._probe_airport_feeds("KABC")
        assert complete is True                  # benign None does not mark incomplete
        assert feeds.get("twr") == "kabc_twr"    # the found feed survives
