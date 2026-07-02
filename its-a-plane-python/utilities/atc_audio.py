"""
atc_audio.py — ATC (LiveATC.net) audio streaming manager.

Singleton, thread-safe. Plays live tower / approach ATC audio for the
airport most relevant to current overhead traffic. Output can be:

  - browser  : the /display mirror plays the LiveATC URL itself (zero Pi cost)
  - usb      : mpv subprocess -> USB-audio-class speaker on the Pi
  - chromecast: pychromecast tells the cast device to pull the URL itself
  - airplay  : pyatv RAOP streams to an AirPlay receiver (on-demand only)

Design constraints (see docs/Flight Tracker - Feature Roadmap.md, O1 review notes):
  * Quiet hours: auto mode never starts audio in the night window; manual may.
  * Probing: ONE ranged GET (Range: bytes=0-256, 2s) with a browser UA; 404 is
    the only "dead" verdict, network errors are unknown, and any 403 opens a
    15-min circuit breaker (LiveATC bans IPs that probe aggressively).
  * One poll: a compact atc dict is exposed via display_state() and folded into
    /api/display-state for the mirror; /api/atc/status stays for the config UI.
  * No proxying: external clients (browser, Chromecast) pull straight from
    LiveATC. Sole exception: the Pi's OWN players (pyatv, which cannot set a
    User-Agent) fetch via a loopback-only relay in web/app.py that adds the
    browser UA — self-fetch for local playback, never a rebroadcast.
  * On-demand AirPlay/Chromecast: the audio stack is spawned only when such an
    output is actively selected and torn down completely on stop/switch — idle
    state is zero processes.

Third-party libs (mpv binary, pychromecast, pyatv, zeroconf) are imported
lazily and guarded so this module (and web/app.py) import cleanly on any host
without them installed.
"""

from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import threading
import time
from datetime import datetime

try:
    import requests
except ImportError:  # requests is a hard dep elsewhere; guard anyway
    requests = None

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# State/cache files live alongside the app's other runtime JSON (current_overhead.json,
# tracked_flight.json, ...) at the project root — overridable for tests/dev hosts.
_DATA_DIR = os.environ.get("PLANE_TRACKER_DATA_DIR", _BASE_DIR)
_SEED_FILE = os.path.join(_BASE_DIR, "data", "atc_stations_seed.json")
_STATE_FILE = os.path.join(_DATA_DIR, "atc_audio.json")
_DISCOVERED_CACHE = os.path.join(_DATA_DIR, "atc_discovered.json")
_OUTPUT_CACHE = os.path.join(_DATA_DIR, "atc_outputs.json")
_AIRPLAY_CREDS = os.path.join(_DATA_DIR, "atc_airplay_creds.json")
# Overhead traffic feed — written by utilities/overhead.py at the project root.
_OVERHEAD_FILE = os.path.join(_BASE_DIR, "current_overhead.json")

# LiveATC direct stream host. d.liveatc.net 302s to a load-balanced icecast
# edge (sN-xxx.liveatc.net) serving audio/mpeg. Do NOT use www.liveatc.net's
# hlisten.php — that is an HTML player page behind a Cloudflare challenge and
# an <audio> element pointed at it gets a 403 page, not a stream.
_LIVEATC_LISTEN = "https://d.liveatc.net/"

# The edges 403 the default python-requests User-Agent, and aggressive
# probing gets the source IP banned outright (observed live) — hence the
# browser UA below and the probe cooldown circuit breaker.
_UA_HEADERS = {"User-Agent": ("Mozilla/5.0 (X11; Linux armv7l) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")}
_PROBE_COOLDOWN_SEC = 900     # stop all probing this long after any 403
_DEAD_FEED_TTL = 6 * 3600     # re-check a dead feed after 6h

# Auto-tune tuning (roadmap O1 "Auto" mode).
_MIN_DWELL_SEC = 180          # 3-minute minimum before switching stations
_SCORE_DECAY_SEC = 120        # 2-minute decay after a flight leaves
_STICKINESS_BONUS = 3         # keep current station unless clearly beaten
_APP_ALT_MIN = 3000           # approach/TRACON preferred 3k-15k ft
_APP_ALT_MAX = 15000
_OUTPUT_RESCAN_TTL = 300      # cache mDNS/airplay discovery for 5 min

# Probe suffixes for airports not in the seed file.
_PROBE_SUFFIXES = ["_app", "_twr", "_gnd_twr", "_app_final", "_dep"]


def _now() -> float:
    return time.time()


def _haversine_mi(lat1, lon1, lat2, lon2):
    R = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic_write(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o666)
        except OSError:
            pass
    except Exception:
        pass


class ATCAudioManager:
    """Singleton manager. Access via get_manager()."""

    def __init__(self):
        self._lock = threading.RLock()
        _seed_raw = _load_json(_SEED_FILE, {})
        self._seed_base = _seed_raw.get("airports", {})
        self._seed = dict(self._seed_base)
        self._custom_raw = None            # last-applied ATC_CUSTOM_FEEDS string
        # ARTCC sector feeds ({id: {name, lat, lon, code}}) — used when the
        # overhead traffic is all high-altitude overflights (center airspace).
        self._centers_base = _seed_raw.get("centers", {})
        self._centers = dict(self._centers_base)
        self._discovered = _load_json(_DISCOVERED_CACHE, {})  # icao -> {feeds, ts}

        # Persisted runtime state. First run (no state file yet): seed from
        # the ATC_* config keys so a configured mode/station/output applies
        # before the live controls are ever touched.
        st = _load_json(_STATE_FILE, {})
        if not st:
            try:
                import config as _cfg
                st = {"mode": getattr(_cfg, "ATC_MODE", "off"),
                      "station": getattr(_cfg, "ATC_STATION", ""),
                      "volume": getattr(_cfg, "ATC_VOLUME", 70),
                      "output": getattr(_cfg, "ATC_OUTPUT", "browser")}
            except Exception:
                st = {}
        self._mode = st.get("mode", "off")            # off | auto | manual
        self._station = st.get("station", "")          # current feed code
        self._volume = int(st.get("volume", 70))       # 0-100
        self._output = st.get("output", "browser")     # unified output id
        self._playing = bool(st.get("playing", False))
        self._last_on_mode = st.get("last_mode", "auto")  # mode to restore on start()
        # A manual start() during quiet hours sets this; auto mode then keeps
        # playing through the window. Cleared when the window ends or on stop().
        self._quiet_override = bool(st.get("quiet_override", False))

        # Auto-tune bookkeeping.
        self._current_since = 0.0

        # Backend process handles (spawned on demand only).
        self._mpv_proc = None
        self._cast_device = None                       # pychromecast device
        self._airplay_stop = None                      # threading.Event for RAOP
        self._airplay_thread = None
        self._airplay_pairing = None                   # active PIN-pairing session

        # Output discovery cache.
        self._outputs_cache = None
        self._outputs_ts = 0.0

        # Probe circuit breaker + runtime dead-feed memory (see _probe_feed).
        self._probe_cooldown_until = 0.0
        self._dead_feeds = {}                          # feed_code -> ts marked dead
        self._station_checked = 0.0                    # last current-station verify

        # Config snapshot (refreshed each tick from config module).
        self._home = (0.0, 0.0)
        self._quiet = ("22:00", "06:00")
        self._auto_resume = True
        self._refresh_config()

        # If we were playing a Pi-side output on restart, honour auto-resume.
        if self._auto_resume and self._playing and self._output in ("usb", "chromecast", "airplay"):
            # Defer actual spawn to first tick() so imports settle.
            self._resume_pending = True
        else:
            self._resume_pending = False
            self._playing = False  # browser playback is re-established by the client

    # ── Config ───────────────────────────────────────────────────────────
    def _refresh_config(self):
        try:
            import config as cfg
            self._home = (float(getattr(cfg, "LOCATION_HOME", [0, 0])[0]),
                          float(getattr(cfg, "LOCATION_HOME", [0, 0])[1]))
            self._home_code = (getattr(cfg, "JOURNEY_CODE_SELECTED", "") or "").strip()
            # ATC_ENABLED master switch
            self._enabled = _cfg_bool(getattr(cfg, "ATC_ENABLED", False))
            # Quiet hours default to the night window.
            night = (getattr(cfg, "NIGHT_START", "22:00"), getattr(cfg, "NIGHT_END", "06:00"))
            raw_quiet = getattr(cfg, "ATC_QUIET_HOURS", "") or ""
            if "-" in raw_quiet:
                a, b = raw_quiet.split("-", 1)
                self._quiet = (a.strip(), b.strip())
            else:
                self._quiet = night
            self._auto_resume = _cfg_bool(getattr(cfg, "ATC_AUTO_RESUME", True))
            # User-added stations: "ICAO/kind/mount[/lat/lon]" comma list —
            # merged over the seed so extra local feeds (or corrections) need
            # no seed-file edit. kind: twr|app|ctr. Without lat/lon the entry
            # ranks at distance 0 (top of the nearby list).
            raw_extra = str(getattr(cfg, "ATC_CUSTOM_FEEDS", "") or "")
            if raw_extra != self._custom_raw:
                self._custom_raw = raw_extra
                self._apply_custom_feeds(raw_extra)
        except Exception:
            self._enabled = False
            self._home_code = getattr(self, "_home_code", "")

    def _apply_custom_feeds(self, raw):
        merged = {k: dict(v, feeds=dict(v.get("feeds", {})))
                  for k, v in self._seed_base.items()}
        centers = dict(self._centers_base)
        for ent in raw.split(","):
            parts = [p.strip() for p in ent.strip().split("/") if p.strip()]
            if len(parts) < 3:
                continue
            key, kind, mount = parts[0].upper(), parts[1].lower(), parts[2]
            lat = lon = None
            if len(parts) >= 5:
                try:
                    lat, lon = float(parts[3]), float(parts[4])
                except ValueError:
                    pass
            if kind == "ctr":
                centers[key] = {"name": key, "code": mount,
                                "lat": lat if lat is not None else self._home[0],
                                "lon": lon if lon is not None else self._home[1]}
                continue
            ap = merged.setdefault(key, {"name": key, "feeds": {},
                                         "lat": self._home[0], "lon": self._home[1]})
            ap.setdefault("feeds", {})[kind] = mount
            if lat is not None:
                ap["lat"], ap["lon"] = lat, lon
        self._seed = merged
        self._centers = centers

    def _in_quiet_hours(self, when=None):
        try:
            now = (when or datetime.now()).strftime("%H:%M")
            start, end = self._quiet
            if start == end:
                return False
            if start < end:
                return start <= now < end
            # Wraps midnight (e.g. 22:00-06:00)
            return now >= start or now < end
        except Exception:
            return False

    # ── Station discovery ────────────────────────────────────────────────
    def _stream_url(self, feed_code):
        """Client-facing stream URL. Clients (browser/cast/airplay) hit LiveATC
        directly; we never proxy. d.liveatc.net 302s to the live icecast edge."""
        return _LIVEATC_LISTEN + feed_code if feed_code else ""

    def _probe_feed(self, feed_code, timeout=2.0):
        """Probe a mount with ONE ranged GET. Returns True (alive), False
        (definitely dead: 404 etc.), or None (unknown — probing is in the
        post-403 cooldown; do not treat as dead and do not cache).
        No HEAD attempt: icecast HEAD is unreliable and every extra request
        raises the ban risk."""
        if requests is None or not feed_code:
            return False
        if _now() < self._probe_cooldown_until:
            return None
        url = self._stream_url(feed_code)
        try:
            r = requests.get(url, timeout=timeout, allow_redirects=True,
                             headers={**_UA_HEADERS, "Range": "bytes=0-256"},
                             stream=True)
            sc = r.status_code
            ct = r.headers.get("Content-Type", "")
            r.close()
            if sc == 403:
                # Rate limit / edge ban — stop ALL probing for a while.
                self._probe_cooldown_until = _now() + _PROBE_COOLDOWN_SEC
                return None
            if 200 <= sc < 300:
                return "audio" in ct or "ogg" in ct or "mpeg" in ct
            if sc == 404:
                return False          # mount genuinely doesn't exist
            return None               # 5xx/302-to-nowhere etc. — unknown
        except Exception:
            # Timeouts / connection-refused edges are NETWORK problems, not
            # proof the mount is dead — post-ban flakiness wrongly dead-marked
            # healthy feeds (incl. kjfk_twr) for 6h. Unknown, never False.
            return None

    def _feed_ok(self, code):
        """Gate a candidate feed before tuning to it. Definitely-dead feeds
        are remembered for _DEAD_FEED_TTL; unknown (cooldown) is optimistic —
        never block playback on an unverifiable probe. The current station is
        trusted without a re-probe."""
        if not code:
            return False
        ts = self._dead_feeds.get(code)
        if ts and (_now() - ts) < _DEAD_FEED_TTL:
            return False
        if code == self._station:
            return True
        v = self._probe_feed(code)
        if v is False:
            self._dead_feeds[code] = _now()
            return False
        return True

    def _feeds_for_airport(self, icao):
        """Return {twr, app, ...} feed dict for an airport: seed first, then
        cached discovery, then live probing of common suffixes."""
        icao = (icao or "").upper()
        if not icao:
            return {}
        if icao in self._seed:
            return self._seed[icao].get("feeds", {})
        cached = self._discovered.get(icao)
        if cached is not None:
            # Empty results are cached too (1 day) — without a negative cache
            # every tick() re-probes ~10 URLs under the lock for an airport
            # that has no LiveATC feeds, stalling /api/atc/* for ~20s each time.
            ttl = 30 * 86400 if cached.get("feeds") else 86400
            if (_now() - cached.get("ts", 0)) < ttl:
                return cached.get("feeds", {})
        # During the post-403 cooldown, don't probe and — critically — don't
        # cache an empty result as "no feeds": we simply can't know right now.
        if _now() < self._probe_cooldown_until:
            return {}
        # Probe common suffixes. LiveATC mounts are usually the lowercase ICAO
        # (kbos_twr) but sometimes drop the K. Our K-prefix guess from a 3-letter
        # code is wrong for Alaska/Hawaii (PANC/PHNL, not KANC/KHNL), so a
        # p-prefixed base is probed too; the negative cache keeps this bounded.
        found = {}
        base = icao.lower()
        bases = [base]
        if base.startswith("k"):
            bases += [base[1:], "p" + base[1:]]
        for suffix in _PROBE_SUFFIXES:
            for b in bases:
                code = f"{b}{suffix}"
                v = self._probe_feed(code)
                if v:
                    kind = "app" if "app" in suffix or "dep" in suffix else "twr"
                    found.setdefault(kind, code)
                    break
                if v is None:
                    # Hit the 403 cooldown mid-sweep: results are incomplete —
                    # return what we have but cache nothing.
                    return found
        self._discovered[icao] = {"feeds": found, "ts": _now()}
        _atomic_write(_DISCOVERED_CACHE, self._discovered)
        return found

    def _fallback_station_locked(self):
        """Default station when no overhead traffic drives the choice.
        Location-based, in order: (1) the HOME airport (JOURNEY_CODE_SELECTED)
        — probing covers airports outside the seed file, so this works
        anywhere LiveATC has a feed; (2) seed airports within 150 mi tried in
        DISTANCE ORDER — the single nearest may have no live feeds (KISP's
        seed mounts don't exist), so keep walking outward. Beyond 150 mi a
        tower is noise, not ambience. Returns (feed_code, icao) or ("", None)."""
        icao = _to_icao(self._home_code)
        if icao:
            feeds = self._feeds_for_airport(icao)
            for kind in ("twr", "app"):
                code = feeds.get(kind, "")
                if code and self._feed_ok(code):
                    return code, icao
        hlat, hlon = self._home
        by_dist = sorted(
            (_haversine_mi(hlat, hlon, info.get("lat", 0), info.get("lon", 0)), icao2)
            for icao2, info in self._seed.items())
        for dist, icao2 in by_dist:
            if dist > 150:
                break
            feeds = self._seed.get(icao2, {}).get("feeds", {})
            for kind in ("twr", "app"):
                code = feeds.get(kind, "")
                if code and self._feed_ok(code):
                    return code, icao2
        return "", None

    # ── Auto-tune ────────────────────────────────────────────────────────
    def _read_overhead(self):
        raw = _load_json(_OVERHEAD_FILE, {})
        if isinstance(raw, list):
            return raw
        return raw.get("flights", [])

    def _nearest_center_feed(self):
        """Nearest ARTCC sector feed to HOME (within 250 mi), gated by
        _feed_ok. Returns (code, center_id) or ("", None)."""
        hlat, hlon = self._home
        by_dist = sorted(
            (_haversine_mi(hlat, hlon, c.get("lat", 0), c.get("lon", 0)), cid)
            for cid, c in self._centers.items())
        for dist, cid in by_dist:
            if dist > 250:
                break
            code = self._centers[cid].get("code", "")
            if code and self._feed_ok(code):
                return code, cid
        return "", None

    def _pick_station_auto(self):
        """Score by AIRPORT (not per-flight) to prevent thrashing. Returns a
        (feed_code, airport_icao) tuple, or (None, None)."""
        flights = self._read_overhead()
        scores = {}   # icao -> score
        prefer_app = {}  # icao -> bool (overhead traffic at altitude)
        high_alt = low_alt = 0
        hlat, hlon = self._home
        for f in flights:
            dest = (f.get("destination") or "").upper()
            orig = (f.get("origin") or "").upper()
            alt = f.get("altitude") or 0
            if alt > _APP_ALT_MAX:
                high_alt += 1
            else:
                low_alt += 1
            for code in (dest, orig):
                icao = _to_icao(code)
                if not icao:
                    continue
                # Relevance filter: a flight overhead bound for an airport
                # 1,000 mi away is NOT talking to that airport's tower — only
                # facilities near HOME can be controlling what we see. Seed
                # airports beyond 250 mi never score; non-seed airports (no
                # coords) stay eligible — probing only finds local ones anyway.
                info = self._seed.get(icao)
                if info and _haversine_mi(hlat, hlon, info.get("lat", 0),
                                          info.get("lon", 0)) > 250:
                    continue
                scores[icao] = scores.get(icao, 0) + (2 if code == dest else 1)
                if _APP_ALT_MIN <= alt <= _APP_ALT_MAX:
                    prefer_app[icao] = True

        # Pure-overflight situation (everything above the approach band):
        # those crews are talking to the ARTCC, not any airport — tune the
        # nearest center sector feed (an ARTCC sector from the seed).
        if flights and high_alt > 0 and low_alt == 0 and self._centers:
            code, cid = self._nearest_center_feed()
            if code:
                return code, cid

        # Decay + stickiness for the current airport.
        now = _now()
        cur_icao = self._station_airport()
        if cur_icao:
            # Score decay: keep some weight for the recently-active station.
            age = now - self._current_since
            if cur_icao not in scores and age < _SCORE_DECAY_SEC:
                scores[cur_icao] = scores.get(cur_icao, 0) + 1
            if cur_icao in scores:
                scores[cur_icao] += _STICKINESS_BONUS

        if not scores:
            return self._fallback_station_locked()

        # Try airports in score order, ties broken by distance from HOME (the
        # nearer facility is the one actually working what we can see) — the
        # top scorer may have no LiveATC feed at all (e.g. a small GA/heliport
        # field); fall through to the next-best, then to the location
        # fallback. Every candidate passes _feed_ok so a stale/wrong mount
        # name self-heals instead of tuning the player to a 404.
        def _dist(icao):
            info = self._seed.get(icao)
            if not info:
                return 0.0   # non-seed = discovered near home
            return _haversine_mi(hlat, hlon, info.get("lat", 0), info.get("lon", 0))
        for icao in sorted(scores, key=lambda i: (-scores[i], _dist(i))):
            feeds = self._feeds_for_airport(icao)
            if not feeds:
                continue
            order = ("app", "twr") if prefer_app.get(icao) else ("twr", "app")
            for kind in order:
                code = feeds.get(kind, "")
                if code and self._feed_ok(code):
                    return code, icao
        return self._fallback_station_locked()

    def _station_airport(self):
        """Best-effort: which airport/center does the current station belong to?"""
        st = self._station
        if not st:
            return None
        for icao, info in self._seed.items():
            if st in info.get("feeds", {}).values():
                return icao
        for cid, c in self._centers.items():
            if st == c.get("code"):
                return cid
        for icao, d in self._discovered.items():
            if st in d.get("feeds", {}).values():
                return icao
        return None

    # ── Output discovery (unified) ───────────────────────────────────────
    def list_outputs(self, force_rescan=False):
        """Return [{id, name, type}] — USB + browser always present; cast +
        airplay from discovery. STALE-WHILE-REVALIDATE: a live mDNS sweep
        takes ~8s, which made the mirror's output popover feel broken —
        so an expired cache is served immediately while a background thread
        refreshes it. rescan=1 still forces a blocking fresh sweep."""
        with self._lock:
            cached = self._outputs_cache
            fresh = cached is not None and \
                (_now() - self._outputs_ts) < _OUTPUT_RESCAN_TTL
        if force_rescan:
            return self._scan_outputs()
        if cached is not None:
            if not fresh:
                threading.Thread(target=self._scan_outputs, daemon=True,
                                 name="atc-output-rescan").start()
            return cached
        return self._scan_outputs()

    def _scan_outputs(self):
        with self._lock:
            if getattr(self, "_scanning_outputs", False):
                return self._outputs_cache or [
                    {"id": "browser", "name": "This browser", "type": "browser"},
                    {"id": "usb", "name": "Pi USB speaker", "type": "usb"},
                ]
            self._scanning_outputs = True
        try:
            outputs = [
                {"id": "browser", "name": "This browser", "type": "browser"},
                {"id": "usb", "name": "Pi USB speaker", "type": "usb"},
            ]
            outputs.extend(self._discover_cast(True))
            outputs.extend(self._discover_airplay(True))
            with self._lock:
                self._outputs_cache = outputs
                self._outputs_ts = _now()
            _atomic_write(_OUTPUT_CACHE, {"outputs": outputs, "ts": _now()})
            return outputs
        finally:
            with self._lock:
                self._scanning_outputs = False

    def _discover_cast(self, force):
        """mDNS Chromecast discovery (incl. speaker groups). Lazy-import; no-op
        if pychromecast/zeroconf absent. Discovery does NOT spawn audio."""
        try:
            import pychromecast  # noqa: F401
        except Exception:
            return _cached_outputs_of_type("chromecast")
        try:
            from pychromecast.discovery import discover_chromecasts
            infos, browser = [], None
            try:
                services, browser = discover_chromecasts(timeout=4)
                infos = services or []
            finally:
                try:
                    if browser:
                        browser.stop_discovery()
                except Exception:
                    pass
            out = []
            for c in infos:
                # CastInfo tuple/obj across versions — read defensively.
                name = getattr(c, "friendly_name", None) or (c[3] if len(c) > 3 else None) or "Chromecast"
                uuid = str(getattr(c, "uuid", None) or (c[1] if len(c) > 1 else name))
                out.append({"id": f"chromecast:{uuid}", "name": name, "type": "chromecast"})
            return out
        except Exception:
            return _cached_outputs_of_type("chromecast")

    def _discover_airplay(self, force):
        """AirPlay RAOP discovery via pyatv (async). Lazy-import; no-op if pyatv
        absent. Discovery scans mDNS only — it does NOT start the audio stack."""
        try:
            import pyatv  # noqa: F401
        except Exception:
            return _cached_outputs_of_type("airplay")
        try:
            import asyncio
            from pyatv import scan as atv_scan

            async def _scan():
                loop = asyncio.get_event_loop()
                results = await atv_scan(loop, timeout=4)
                out = []
                for dev in results:
                    # Only devices exposing a RAOP (AirPlay audio) service.
                    has_raop = any(
                        getattr(s, "protocol", None).__str__().lower().find("raop") >= 0
                        for s in getattr(dev, "services", [])
                    )
                    if has_raop:
                        out.append({
                            "id": f"airplay:{dev.identifier}",
                            "name": dev.name, "type": "airplay",
                        })
                return out

            return _run_async(_scan())
        except Exception:
            return _cached_outputs_of_type("airplay")

    # ── Public state ─────────────────────────────────────────────────────
    def status(self):
        """Full status for the config UI + HomeKit scripts."""
        with self._lock:
            code = self._station
            return {
                "enabled": self._enabled,
                "mode": self._mode,
                "station": code,
                "station_airport": self._station_airport(),
                "stream_url": self._stream_url(code),
                "volume": self._volume,
                "output": self._output,
                "playing": self._playing,
                "quiet_hours": f"{self._quiet[0]}-{self._quiet[1]}",
                "in_quiet_hours": self._in_quiet_hours(),
            }

    def display_state(self):
        """Compact object folded into /api/display-state for the mirror.
        Only what the browser <audio> element needs (review note 4).
        stream_url is exposed whenever browser output is configured — even
        while server-side stopped (e.g. quiet hours) — so the mirror's play
        button can override in the same click gesture; `playing` is what
        mirrors actual playback intent."""
        with self._lock:
            browser_cfg = self._output == "browser" and self._mode != "off"
            return {
                "enabled": bool(self._enabled),
                "mode": self._mode,
                "station": self._station,
                "stream_url": self._stream_url(self._station) if browser_cfg else "",
                # True whenever the server is playing on ANY output — the
                # mirror needs cast/usb state for its play button and LIVE tag
                # (browser playback additionally requires stream_url above).
                "playing": bool(self._playing and self._mode != "off"),
                "in_quiet_hours": self._in_quiet_hours(),
                "output": self._output,
                "volume": self._volume,
            }

    def _persist(self):
        _atomic_write(_STATE_FILE, {
            "mode": self._mode, "station": self._station, "volume": self._volume,
            "output": self._output, "playing": self._playing,
            "last_mode": self._last_on_mode,
            "quiet_override": self._quiet_override,
        })

    # ── Controls ─────────────────────────────────────────────────────────
    def set_mode(self, mode):
        if mode not in ("off", "auto", "manual"):
            return self.status()
        with self._lock:
            self._mode = mode
            if mode == "off":
                self._quiet_override = False
                self._stop_locked()
            self._persist()
        self.tick()
        return self.status()

    def set_station(self, feed_code):
        code = (feed_code or "").strip()
        with self._lock:
            # Verify at selection time — a demonstrably dead mount gets a
            # visible refusal instead of tuning the player to a 404 (stale
            # UI lists can still offer since-removed mounts).
            if code and self._probe_feed(code) is False:
                self._dead_feeds[code] = _now()
                st = self.status()
                st["error"] = f"'{code}' is offline at LiveATC"
                return st
            self._station = code
            self._current_since = _now()
            self._mode = "manual" if self._station else self._mode
            # Re-point any Pi-side backend at the new station.
            if self._playing and self._output in ("usb", "chromecast", "airplay"):
                self._stop_backend_locked()
                self._start_backend_locked()
            self._persist()
        return self.status()

    def set_volume(self, vol):
        with self._lock:
            self._volume = max(0, min(100, int(vol)))
            if self._mpv_proc:
                self._mpv_set_volume(self._volume)
            self._persist()
        return self.status()

    def select_output(self, output_id):
        with self._lock:
            if output_id == self._output:
                return self.status()
            was_playing = self._playing
            # Tear down the old backend completely before switching.
            self._stop_backend_locked()
            self._output = output_id
            self._persist()
            # If we were playing, bring up the new backend (unless browser —
            # the browser establishes its own playback from display-state).
            if was_playing and self._mode != "off":
                if output_id == "browser":
                    self._playing = True
                else:
                    self._start_backend_locked()
        return self.status()

    def start(self):
        """Explicit start (HomeKit on.sh / UI play)."""
        with self._lock:
            if self._mode == "off":
                # Restore the mode that was active before the last stop();
                # fall back to manual-if-station-set, else auto.
                if self._last_on_mode in ("auto", "manual"):
                    self._mode = self._last_on_mode
                else:
                    self._mode = "manual" if self._station else "auto"
            self._ensure_station_locked()
            self._playing = True
            # Playing again during the quiet window is an explicit override:
            # the auto-mode gate honours it until the window ends or stop().
            if self._in_quiet_hours():
                self._quiet_override = True
            if self._output in ("usb", "chromecast", "airplay"):
                self._start_backend_locked()
            self._persist()
        return self.status()

    def stop(self):
        """Explicit stop (HomeKit off.sh / UI pause). Must STICK: in auto mode
        tick() restarts playback within seconds, so a public stop also drops
        the mode to off (start() restores it via _last_on_mode). The internal
        _stop_locked() — used by quiet hours — deliberately keeps the mode so
        auto can resume after the window."""
        with self._lock:
            if self._mode != "off":
                self._last_on_mode = self._mode
                self._mode = "off"
            self._quiet_override = False
            self._stop_locked()
            self._persist()
        return self.status()

    def _stop_locked(self):
        self._playing = False
        self._stop_backend_locked()

    def _ensure_station_locked(self):
        if self._station:
            return
        if self._mode == "auto":
            code, icao = self._pick_station_auto()
            self._station = code or ""
        if not self._station:
            code, icao = self._fallback_station_locked()
            self._station = code or ""
        self._current_since = _now()

    # ── Auto-tune tick (called periodically off the display hot loop) ─────
    def tick(self):
        """Advance the auto-tuner and honour deferred resume. Safe to call
        every few seconds from a background thread; never from the LED loop."""
        self._refresh_config()
        with self._lock:
            if self._resume_pending:
                self._resume_pending = False
                if self._playing and self._output in ("usb", "chromecast", "airplay"):
                    self._start_backend_locked()

            if not self._enabled or self._mode == "off":
                if self._playing:
                    self._stop_locked()
                return

            # Re-verify the current station every 10 min — a persisted or
            # previously-picked mount can be dead (or die mid-listen).
            # _feed_ok trusts the current station, so this is the only
            # recovery path. Applies to manual too: when a hand-picked feed
            # dies we fall back to auto so the radio keeps working instead
            # of ERRing forever.
            if self._station and (_now() - self._station_checked) > 600:
                self._station_checked = _now()
                if self._probe_feed(self._station) is False:
                    self._dead_feeds[self._station] = _now()
                    self._station = ""
                    if self._mode == "manual":
                        self._mode = "auto"

            if self._mode == "auto":
                # Quiet hours gate — auto mode must not start audio at 2am.
                # A start() during the window sets _quiet_override, so "play
                # again" wins until the window ends or an explicit stop().
                in_quiet = self._in_quiet_hours()
                if not in_quiet:
                    self._quiet_override = False  # window over; re-arm for tonight
                if in_quiet and not self._quiet_override:
                    if self._playing:
                        self._stop_locked()
                    # Keep a station selected while quieted so the mirror and
                    # config UI can show what WOULD play and offer the override.
                    if not self._station:
                        code, _icao = self._pick_station_auto()
                        if code:
                            self._station = code
                            self._current_since = _now()
                    self._persist()
                    return
                code, icao = self._pick_station_auto()
                if code:
                    changed = code != self._station
                    dwell_ok = (_now() - self._current_since) >= _MIN_DWELL_SEC
                    if changed and (dwell_ok or not self._station):
                        self._station = code
                        self._current_since = _now()
                        if self._playing and self._output in ("usb", "chromecast", "airplay"):
                            self._stop_backend_locked()
                            self._start_backend_locked()
                # Arm playback only when there is actually a station — no
                # point reporting "playing" silence when nothing resolved.
                if self._station and not self._playing:
                    self._playing = True
                    if self._output in ("usb", "chromecast", "airplay"):
                        self._start_backend_locked()

            # manual mode: station is user-locked; nothing to auto-advance.
            self._persist()

    # ── Backend: mpv (USB speaker) ───────────────────────────────────────
    # HARDWARE-VERIFY PENDING: the Adafruit #3369 USB speaker has not arrived,
    # so the mpv/USB path below is code-complete but NOT yet hardware-verified.
    # TODO(verify-usb): confirm on-Pi that mpv plays through the USB-audio-class
    # device with snd_bcm2835 blacklisted (the only ALSA device). See roadmap
    # O1 "Audio output — HARDWARE CONSTRAINT".
    _MPV_IPC = os.path.join(_DATA_DIR, "atc_mpv.sock")

    def _start_mpv_locked(self):
        url = self._stream_url(self._station)
        if not url:
            return
        try:
            try:
                os.remove(self._MPV_IPC)
            except OSError:
                pass
            self._mpv_proc = subprocess.Popen(
                ["mpv", "--no-video", "--no-terminal", "--idle=no",
                 "--user-agent=" + _UA_HEADERS["User-Agent"],
                 f"--volume={self._volume}",
                 f"--input-ipc-server={self._MPV_IPC}", url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            # Pin to core 2 (leave core 3 for the LED display) — best effort.
            try:
                os.sched_setaffinity(self._mpv_proc.pid, {2})
            except Exception:
                pass
        except FileNotFoundError:
            # mpv not installed (e.g. dev host) — degrade gracefully.
            self._mpv_proc = None
        except Exception:
            self._mpv_proc = None

    def _mpv_set_volume(self, vol):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect(self._MPV_IPC)
            cmd = json.dumps({"command": ["set_property", "volume", vol]}) + "\n"
            s.sendall(cmd.encode())
            s.close()
        except Exception:
            pass

    def _stop_mpv_locked(self):
        if self._mpv_proc:
            try:
                self._mpv_proc.terminate()
                try:
                    self._mpv_proc.wait(timeout=3)
                except Exception:
                    self._mpv_proc.kill()
            except Exception:
                pass
            self._mpv_proc = None
        try:
            os.remove(self._MPV_IPC)
        except OSError:
            pass

    # ── Backend: Chromecast (cast pulls the URL itself) ──────────────────
    def _start_cast_locked(self):
        """Tell the selected cast device to pull the LiveATC URL itself. No Pi
        audio pipeline is involved (review note 5)."""
        url = self._stream_url(self._station)
        uuid = self._output.split(":", 1)[1] if ":" in self._output else ""
        if not url or not uuid:
            return
        try:
            import pychromecast
        except Exception:
            return
        try:
            # pychromecast matches UUID objects, not strings — a string uuid
            # silently finds nothing (casts were never commanded). Try the
            # UUID object first, then fall back to the friendly name from the
            # outputs cache.
            import uuid as _uuid_mod
            browser = None
            try:
                casts, browser = pychromecast.get_listed_chromecasts(
                    uuids=[_uuid_mod.UUID(uuid)])
            except Exception:
                casts = []
            if not casts:
                try:
                    if browser:
                        browser.stop_discovery()
                except Exception:
                    pass
                name = next((o.get("name") for o in (self._outputs_cache or [])
                             if o.get("id") == self._output), None)
                if not name:
                    return
                casts, browser = pychromecast.get_listed_chromecasts(
                    friendly_names=[name])
            dev = casts[0] if casts else None
            if dev is None:
                return
            dev.wait(timeout=5)
            # If another app owns the device (e.g. Pandora), media commands
            # land on ITS session instead of launching ours — quit it first.
            try:
                if dev.status and dev.status.display_name and \
                        dev.status.display_name not in ("Backdrop", None, ""):
                    dev.quit_app()
                    import time as _t
                    _t.sleep(3)
            except Exception:
                pass
            dev.set_volume(self._volume / 100.0)
            mc = dev.media_controller
            # play_media autoplays on load; an extra play() raises
            # "no session" whenever the load failed — don't call it.
            mc.play_media(url, "audio/mpeg")
            mc.block_until_active(timeout=8)
            self._cast_device = dev
            try:
                if browser:
                    browser.stop_discovery()
            except Exception:
                pass
        except Exception:
            self._cast_device = None

    def _stop_cast_locked(self):
        if self._cast_device is not None:
            try:
                self._cast_device.media_controller.stop()
                self._cast_device.quit_app()
                self._cast_device.disconnect()
            except Exception:
                pass
            self._cast_device = None

    # ── Backend: AirPlay (pyatv RAOP, on-demand only) ────────────────────
    def _start_airplay_locked(self):
        """Stream to an AirPlay receiver via pyatv RAOP. Spawned ONLY here, when
        an AirPlay output is active; torn down fully on stop. No resident
        PulseAudio / no resident process (brief Section 3)."""
        # pyatv cannot set a User-Agent and LiveATC 403s library UAs, so the
        # AirPlay fetch goes through the Pi's own loopback relay (adds a
        # browser UA; refuses non-local clients — not a rebroadcast).
        url = (f"http://127.0.0.1:8080/atc/relay?code={self._station}"
               if self._station else "")
        ident = self._output.split(":", 1)[1] if ":" in self._output else ""
        if not url or not ident:
            return
        try:
            import pyatv  # noqa: F401
        except Exception:
            return
        stop_evt = threading.Event()
        self._airplay_stop = stop_evt

        def _run():
            import asyncio
            from pyatv import scan as atv_scan, connect as atv_connect

            async def _stream():
                loop = asyncio.get_event_loop()
                results = await atv_scan(loop, identifier=ident, timeout=5)
                if not results:
                    return
                conf = results[0]
                # Stored pairing credentials (devices that ask for a code).
                stored = _load_json(_AIRPLAY_CREDS, {}).get(ident)
                if stored:
                    try:
                        from pyatv.const import Protocol
                        conf.set_credentials(Protocol.RAOP, stored)
                    except Exception:
                        pass
                atv = await atv_connect(conf, loop)
                try:
                    # stream_url pulls/pushes the URL to the receiver; it returns
                    # when playback ends. We poll stop_evt to allow teardown.
                    task = asyncio.ensure_future(atv.stream.stream_file(url))
                    while not stop_evt.is_set() and not task.done():
                        await asyncio.sleep(0.25)
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except Exception:
                            pass
                finally:
                    try:
                        await atv.close()
                    except Exception:
                        pass

            try:
                asyncio.new_event_loop().run_until_complete(_stream())
            except Exception:
                pass

        self._airplay_thread = threading.Thread(target=_run, daemon=True)
        self._airplay_thread.start()

    def _stop_airplay_locked(self):
        if self._airplay_stop is not None:
            self._airplay_stop.set()
        t = self._airplay_thread
        if t is not None:
            t.join(timeout=5)
        self._airplay_stop = None
        self._airplay_thread = None

    # ── AirPlay pairing (devices that ask for a code) ────────────────────
    # HomePods / passworded receivers require a one-time RAOP pairing: begin
    # -> device shows a PIN -> finish(pin) -> credentials stored and used on
    # every subsequent stream. Two HTTP calls bridge one async session via a
    # dedicated thread + events.
    def airplay_pair_begin(self, output_id):
        ident = output_id.split(":", 1)[1] if ":" in output_id else output_id
        try:
            import pyatv  # noqa: F401
        except Exception:
            return {"ok": False, "error": "pyatv not installed"}
        self.airplay_pair_cancel()
        state = {"ident": ident, "stage": "starting", "error": "",
                 "pin_event": threading.Event(), "pin": None,
                 "done_event": threading.Event()}
        self._airplay_pairing = state

        def _run():
            import asyncio
            from pyatv import scan as atv_scan, pair as atv_pair
            from pyatv.const import Protocol

            async def _flow():
                loop = asyncio.get_event_loop()
                results = await atv_scan(loop, identifier=ident, timeout=6)
                if not results:
                    state.update(stage="error", error="device not found")
                    return
                pairing = await atv_pair(results[0], Protocol.RAOP, loop)
                try:
                    await pairing.begin()
                    if pairing.device_provides_pin:
                        state["stage"] = "awaiting_pin"   # PIN shows ON the device
                        # Wait (in executor) for finish(pin) to supply it.
                        ok = await loop.run_in_executor(
                            None, state["pin_event"].wait, 120)
                        if not ok:
                            state.update(stage="error", error="PIN entry timed out")
                            return
                        pairing.pin(state["pin"])
                    await pairing.finish()
                    if pairing.has_paired:
                        creds = _load_json(_AIRPLAY_CREDS, {})
                        creds[ident] = pairing.service.credentials
                        _atomic_write(_AIRPLAY_CREDS, creds)
                        state["stage"] = "paired"
                    else:
                        state.update(stage="error", error="pairing not accepted")
                finally:
                    try:
                        await pairing.close()
                    except Exception:
                        pass

            try:
                asyncio.new_event_loop().run_until_complete(_flow())
            except Exception as e:
                state.update(stage="error", error=str(e)[:120])
            finally:
                state["done_event"].set()

        threading.Thread(target=_run, daemon=True, name="atc-airplay-pair").start()
        # Give the flow a moment to reach the PIN stage for a useful response.
        for _ in range(40):
            if state["stage"] in ("awaiting_pin", "paired", "error"):
                break
            time.sleep(0.25)
        return {"ok": state["stage"] != "error", "stage": state["stage"],
                "error": state["error"]}

    def airplay_pair_finish(self, pin):
        state = self._airplay_pairing
        if not state or state["stage"] != "awaiting_pin":
            return {"ok": False, "error": "no pairing awaiting a PIN"}
        state["pin"] = str(pin).strip()
        state["pin_event"].set()
        state["done_event"].wait(30)
        return {"ok": state["stage"] == "paired", "stage": state["stage"],
                "error": state["error"]}

    def airplay_pair_cancel(self):
        state = getattr(self, "_airplay_pairing", None)
        if state:
            state["pin_event"].set()
            state["done_event"].wait(2)
        self._airplay_pairing = None
        return {"ok": True}

    # ── Backend dispatch ─────────────────────────────────────────────────
    def _start_backend_locked(self):
        # Ensure no stale backend is running first.
        self._stop_backend_locked()
        if not self._station:
            self._ensure_station_locked()
        out = self._output
        if out == "usb":
            self._start_mpv_locked()
        elif out.startswith("chromecast"):
            self._start_cast_locked()
        elif out.startswith("airplay"):
            self._start_airplay_locked()
        # browser: nothing to spawn — the mirror plays it client-side.

    def _stop_backend_locked(self):
        self._stop_mpv_locked()
        self._stop_cast_locked()
        self._stop_airplay_locked()

    def stations(self):
        """Seed + centers for the manual-select dropdown."""
        out = []
        for icao, info in sorted(self._seed.items()):
            for kind, code in info.get("feeds", {}).items():
                out.append({"code": code, "airport": icao,
                            "name": info.get("name", icao), "type": kind})
        for cid, c in sorted(self._centers.items()):
            out.append({"code": c.get("code", ""), "airport": cid,
                        "name": c.get("name", cid), "type": "ctr"})
        return out

    _KIND_LABELS = {"twr": "Tower", "app": "Approach", "ctr": "Center"}

    def nearby_stations(self, limit=8):
        """Distance-ordered airport/feed list for the selector UI (O2).
        PASSIVE: built from the seed, the discovery cache, and dead-feed
        memory only — listing must never generate LiveATC traffic. Dead-marked
        mounts are hidden so the dropdown never offers silence."""
        with self._lock:
            hlat, hlon = self._home
            def alive(code):
                ts = self._dead_feeds.get(code)
                return not (ts and (_now() - ts) < _DEAD_FEED_TTL)
            entries = []
            # Home airport first when discovery has already found feeds for it.
            home_icao = _to_icao(self._home_code)
            if home_icao and home_icao not in self._seed:
                cached = self._discovered.get(home_icao, {}).get("feeds", {})
                if cached:
                    entries.append((0.0, home_icao,
                                    {"name": self._home_code, "feeds": cached}))
            for icao, info in self._seed.items():
                d = _haversine_mi(hlat, hlon, info.get("lat", 0), info.get("lon", 0))
                entries.append((d, icao, info))
            # ARTCC sector feeds rank alongside airports by distance.
            for cid, c in self._centers.items():
                d = _haversine_mi(hlat, hlon, c.get("lat", 0), c.get("lon", 0))
                entries.append((d, cid,
                                {"name": c.get("name", cid),
                                 "feeds": {"ctr": c.get("code", "")}}))
            entries.sort(key=lambda t: t[0])
            out = []
            for d, icao, info in entries:
                if d > 150 or len(out) >= limit:
                    break
                feeds = [{"kind": k, "code": c,
                          "label": self._KIND_LABELS.get(k, k)}
                         for k, c in info.get("feeds", {}).items()
                         if c and alive(c)]
                if feeds:
                    out.append({"icao": icao, "name": info.get("name", icao),
                                "dist_mi": int(round(d)), "feeds": feeds})
            return out


# ── Module-level helpers ────────────────────────────────────────────────
def _cfg_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _to_icao(code):
    """Normalise a 3/4-letter airport code to a seed ICAO key best-effort."""
    if not code:
        return ""
    code = code.upper()
    if len(code) == 4:
        return code
    if len(code) == 3:
        return "K" + code  # US-centric; matches the seed file's K-prefixed keys
    return ""


def _cached_outputs_of_type(kind):
    cached = _load_json(_OUTPUT_CACHE, {}).get("outputs", [])
    return [o for o in cached if o.get("type") == kind]


def _run_async(coro):
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    except Exception:
        return []


_manager = None
_manager_lock = threading.Lock()


def get_manager():
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = ATCAudioManager()
    return _manager
