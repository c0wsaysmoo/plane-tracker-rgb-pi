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
_DATA_DIR = os.environ.get("PLANE_TRACKER_DATA_DIR", "/var/lib/plane-tracker")
_SEED_FILE = os.path.join(_BASE_DIR, "data", "atc_stations_seed.json")
_STATE_FILE = os.path.join(_DATA_DIR, "atc_audio.json")
_DISCOVERED_CACHE = os.path.join(_DATA_DIR, "atc_discovered.json")
_OUTPUT_CACHE = os.path.join(_DATA_DIR, "atc_outputs.json")
_AIRPLAY_CREDS = os.path.join(_DATA_DIR, "atc_airplay_creds.json")
_OVERHEAD_FILE = os.path.join(_DATA_DIR, "current_overhead.json")

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


def _atomic_write(path, data, mode=0o666):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        # Set perms on the TEMP file before it's renamed into place, so the
        # final path is never briefly world-readable — important for the
        # pairing-secrets file (mode=0o600). Default 0o666 keeps the shared
        # display/web data files writable by both processes.
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        pass


class ATCAudioManager:
    """Singleton manager. Access via get_manager()."""

    @staticmethod
    def _pi_side(output):
        """True for outputs whose backend WE run (usb / chromecast:<uuid> /
        airplay:<id>) vs the browser playing client-side. NOTE: cast and
        airplay ids carry suffixes — an exact `in (...)` match silently
        never started their backends (start/tick/resume all had this bug)."""
        return output == "usb" or output.startswith(("chromecast", "airplay"))

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
        # Background feed-discovery: _feeds_for_airport enqueues cache-misses
        # here and a worker probes them OFF-lock, so auto-tune never fires a
        # ~20s probe sweep while holding self._lock (which stalled status() and
        # the mirror poll).
        self._discover_queue = set()          # icaos pending discovery
        self._discover_lock = threading.Lock()
        self._discover_thread = None

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
        # Last-persisted snapshot so _persist() can skip identical writes —
        # tick() calls it every 5s and an unconditional atomic write wore
        # the SD card for no reason.
        self._persisted = None

        # Auto-tune bookkeeping.
        self._current_since = 0.0

        # Backend process handles (spawned on demand only).
        self._mpv_proc = None
        self._cast_device = None                       # pychromecast device
        self._cast_stop = None                         # threading.Event for cast start worker
        self._cast_thread = None
        self._airplay_stop = None                      # threading.Event for RAOP
        self._airplay_thread = None
        self._airplay_pairing = None                   # active PIN-pairing session
        # Pairing/failure flags are RESTORED from the state file: without
        # this, auto-resume relaunched an unpaired AirPlay target on every
        # boot and re-popped the receiver's code screen (470 loop).
        self._airplay_needs_pairing = st.get("airplay_needs_pairing", "")  # ident that 470'd
        self._airplay_failed = st.get("airplay_failed", "")  # ident whose reconnect loop gave up
        # Throttle for tick()'s backend supervision — a permanently-dead
        # device must not be re-spawned on every 5s tick.
        self._backend_retry_ts = 0.0

        # Output discovery cache — SEED from the last persisted scan so the
        # first output-popover after a restart doesn't block ~8s on a cold mDNS
        # sweep. list_outputs() serves this immediately (even if stale) and
        # refreshes in the background.
        _oc = _load_json(_OUTPUT_CACHE, {})
        self._outputs_cache = _oc.get("outputs") or None
        self._outputs_ts = _oc.get("ts", 0.0)

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
        if self._auto_resume and self._playing and self._pi_side(self._output):
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
            # USB speaker ALSA device for mpv: explicit override like
            # "alsa/plughw:CARD=UACDemoV10,DEV=0"; blank = auto-detect the first
            # USB-audio card at play time. Without a device mpv plays to the
            # Pi's onboard jack, so "USB output" was silent even with a speaker.
            self._usb_device = (getattr(cfg, "ATC_USB_DEVICE", "") or "").strip()
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

    @staticmethod
    def _hhmm_to_min(s):
        """'HH:MM' -> minutes since midnight, or None. Tolerates a missing
        zero-pad ('6:00'), which broke the old lexicographic compare:
        '22:00' < '6:00' is true, so a '22:00-6:00' window never wrapped and
        auto ATC played at 2am."""
        try:
            h, m = str(s).strip().split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return None

    def _in_quiet_hours(self, when=None):
        try:
            start, end = self._quiet
            a = self._hhmm_to_min(start)
            b = self._hhmm_to_min(end)
            if a is None or b is None or a == b:
                return False
            now = when or datetime.now()
            cur = now.hour * 60 + now.minute
            if a < b:
                return a <= cur < b
            return cur >= a or cur < b   # wraps midnight
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
        """Return {twr, app, ...} for an airport WITHOUT any network probe:
        seed first, then the discovery cache. A cache miss is handed to the
        background discovery worker (probing under self._lock previously stalled
        status()/the mirror ~20s per cache-cold airport) and returns {} for now;
        the next tick (~5s) picks up the worker's cached result."""
        icao = (icao or "").upper()
        if not icao:
            return {}
        if icao in self._seed:
            return self._seed[icao].get("feeds", {})
        cached = self._discovered.get(icao)
        if cached is not None:
            # Empty results are cached too (1 day) — the negative cache keeps a
            # feedless airport from being re-queued for probing every tick.
            ttl = 30 * 86400 if cached.get("feeds") else 86400
            if (_now() - cached.get("ts", 0)) < ttl:
                return cached.get("feeds", {})
        # Not cached: queue for OFF-lock discovery (never probe here). Skip
        # during the post-403 cooldown so we don't queue work we can't do.
        if _now() >= self._probe_cooldown_until:
            self._enqueue_discovery(icao)
        return {}

    def _enqueue_discovery(self, icao):
        """Queue an airport for background feed discovery and (re)start the
        worker. Safe to call under self._lock — only touches the small queue."""
        with self._discover_lock:
            self._discover_queue.add(icao)
            if self._discover_thread is None or not self._discover_thread.is_alive():
                try:
                    self._discover_thread = threading.Thread(
                        target=self._discover_worker, daemon=True, name="atc-discover")
                    self._discover_thread.start()
                except Exception:
                    self._discover_thread = None   # OOM — retry on next enqueue

    def _discover_worker(self):
        """Probe queued airports' feeds OFF-lock, then cache the result. Gentle:
        one airport at a time with a small pause, and it honours the 403
        cooldown so a burst of new overhead airports can't hammer LiveATC."""
        while True:
            with self._discover_lock:
                if not self._discover_queue:
                    self._discover_thread = None
                    return
                icao = self._discover_queue.pop()
            if _now() < self._probe_cooldown_until:
                with self._discover_lock:      # in cooldown — requeue, back off
                    self._discover_queue.add(icao)
                time.sleep(5)
                continue
            feeds, complete = self._probe_airport_feeds(icao)
            if complete:
                with self._lock:               # brief, no network
                    self._discovered[icao] = {"feeds": feeds, "ts": _now()}
                try:
                    _atomic_write(_DISCOVERED_CACHE, self._discovered)
                except Exception:
                    pass
            time.sleep(1.0)                     # gentle pacing between airports

    def _probe_airport_feeds(self, icao):
        """The actual suffix-probe sweep for a non-seed airport — runs ONLY in
        the discovery worker (off-lock). Returns (feeds, complete); complete is
        False if the 403 cooldown was hit mid-sweep (caller must not cache).

        LiveATC mounts are usually the lowercase ICAO (kbos_twr) but sometimes
        drop the K; a K-prefix guess is wrong for Alaska/Hawaii (PANC/PHNL), so
        a p-prefixed base is probed too."""
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
                if v is None and _now() < self._probe_cooldown_until:
                    # The 403 cooldown tripped mid-sweep — we're rate-limited, so
                    # the sweep is genuinely incomplete: hand back what we found
                    # but let the caller skip caching (a later un-banned sweep
                    # completes it). A per-mount timeout/5xx is also None but does
                    # NOT set the cooldown; that is only THAT mount's problem, so
                    # keep sweeping — otherwise one flaky probe throws away a live
                    # feed already found and the airport re-enqueues every tick.
                    return found, False
        return found, True

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
        (feed_code, airport_icao) tuple, or (None, None).

        Feed DISCOVERY (the ~15-URL sweep for a non-seed airport) now runs in a
        background worker — _feeds_for_airport only reads the cache or enqueues,
        so it never probes under self._lock. The single per-candidate
        _feed_ok() verify-probe (~2s, only on a station CHANGE to a not-yet-seen
        feed) is the sole remaining under-lock network call; it's dwell-throttled
        and acceptable."""
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

    def _station_label(self):
        """Human 'KJFK Tower' / 'ZBW Hampton Center'-style label for the
        current station, for the 'now playing' metadata sent to Chromecast /
        AirPlay receivers and the browser MediaSession. Reverse-looks the
        station code back to its airport/center + feed kind."""
        st = self._station
        if not st:
            return "ATC radio"
        for icao, info in self._seed.items():
            for k, c in info.get("feeds", {}).items():
                if c == st:
                    return f"{icao} {self._KIND_LABELS.get(k, k.title())}"
        for cid, c in self._centers.items():
            if st == c.get("code"):
                # Seed center names already read like "Boston Center —
                # Hampton 31 (eastern LI overflights)": blindly appending
                # " Center" doubled the word and overflowed the receivers'
                # now-playing line. Keep the first segment, cap the length,
                # and only add "Center" when it isn't already in the name.
                name = c.get("name", cid).split(" — ", 1)[0].strip()
                if "center" not in name.lower():
                    name = f"{name} Center"
                return name[:24]
        for icao, d in self._discovered.items():
            for k, c in d.get("feeds", {}).items():
                if c == st:
                    return f"{icao} {self._KIND_LABELS.get(k, k.title())}"
        ap = self._station_airport()
        return f"{ap} ATC" if ap else st.upper()

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
            # Cast and AirPlay mDNS sweeps are independent 4s scans — run them
            # concurrently (~4s total) instead of back-to-back (~8s).
            _cast, _air = [[]], [[]]

            def _run_cast():
                try:
                    _cast[0] = self._discover_cast(True)
                except Exception:
                    pass

            def _run_air():
                try:
                    _air[0] = self._discover_airplay(True)
                except Exception:
                    pass

            tc = threading.Thread(target=_run_cast, name="atc-scan-cast")
            ta = threading.Thread(target=_run_air, name="atc-scan-airplay")
            tc.start(); ta.start()
            tc.join(timeout=10); ta.join(timeout=10)
            outputs.extend(_cast[0])
            outputs.extend(_air[0])
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
                "station_label": self._station_label(),
                "stream_url": self._stream_url(code),
                "volume": self._volume,
                "output": self._output,
                "playing": self._playing,
                "quiet_hours": f"{self._quiet[0]}-{self._quiet[1]}",
                "in_quiet_hours": self._in_quiet_hours(),
                "airplay_needs_pairing": self._airplay_needs_pairing,
                "airplay_failed": self._airplay_failed,
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
                "airplay_needs_pairing": self._airplay_needs_pairing,
                "airplay_failed": self._airplay_failed,
                "station_label": self._station_label(),
            }

    def _persist(self):
        data = {
            "mode": self._mode, "station": self._station, "volume": self._volume,
            "output": self._output, "playing": self._playing,
            "last_mode": self._last_on_mode,
            "quiet_override": self._quiet_override,
            # Persisting playing:true WITHOUT these flags meant a restart
            # auto-resumed an unpaired/failed AirPlay target and re-popped
            # the receiver's code screen; __init__ restores them.
            "airplay_needs_pairing": self._airplay_needs_pairing,
            "airplay_failed": self._airplay_failed,
        }
        # Skip identical writes: tick() persists every 5s, and rewriting an
        # unchanged file forever is pure SD-card wear.
        if data == self._persisted:
            return
        self._persisted = data
        _atomic_write(_STATE_FILE, dict(data))

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
        # Verify at selection time — a demonstrably dead mount gets a
        # visible refusal instead of tuning the player to a 404 (stale
        # UI lists can still offer since-removed mounts). Probed OUTSIDE
        # the lock: the ranged GET can take 2s and status()/display_state()
        # (and the mirror's 2s poll) must not stall behind it.
        if code and self._probe_feed(code) is False:
            with self._lock:
                self._dead_feeds[code] = _now()
            st = self.status()
            st["error"] = f"'{code}' is offline at LiveATC"
            return st
        with self._lock:
            self._station = code
            self._current_since = _now()
            self._mode = "manual" if self._station else self._mode
            # Re-point any Pi-side backend at the new station.
            if self._playing and self._pi_side(self._output):
                self._stop_backend_locked()
                self._start_backend_locked()
            self._persist()
        return self.status()

    def set_volume(self, vol):
        with self._lock:
            self._volume = max(0, min(100, int(vol)))
            v = self._volume
            if self._mpv_proc:
                self._mpv_set_volume(v)
            cast_dev = self._cast_device
            self._persist()
        # Propagate live to a running cast session OUTSIDE the lock (it's a
        # network command). Before, cast/airplay volume was only set once at
        # session start, so dragging the slider mid-stream did nothing on the
        # speaker. AirPlay is handled by its worker's 0.25s poll of self._volume
        # (below) — no cross-thread call needed here.
        if cast_dev is not None:
            try:
                cast_dev.set_volume(v / 100.0)
            except Exception as e:
                print(f"ATC cast: set_volume failed: {e}", flush=True)
        return self.status()

    def select_output(self, output_id):
        # Only accept ids we can actually dispatch to — an arbitrary string
        # persisted here parked the manager on an output no backend matches
        # (silence until someone re-selects). Checked BEFORE the lock:
        # list_outputs() may do a blocking mDNS sweep on a cold cache.
        if output_id not in ("browser", "usb") and \
                output_id not in {o.get("id") for o in (self.list_outputs() or [])}:
            return self.status()
        with self._lock:
            if output_id == self._output:
                return self.status()
            was_playing = self._playing
            # Tear down the old backend completely before switching.
            self._stop_backend_locked()
            self._output = output_id
            self._airplay_needs_pairing = ""   # new target — allow a fresh attempt
            self._airplay_failed = ""
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
        self._refresh_config()
        with self._lock:
            # Refuse when the feature is disabled — otherwise start() picked a
            # station (probing), set _playing, and spawned a backend that the
            # next tick() immediately tore down, so a HomeKit ON while disabled
            # flapped the tile true->false and burned a probe/spawn cycle.
            if not self._enabled:
                return self.status()
            if self._mode == "off":
                # Restore the mode that was active before the last stop();
                # fall back to manual-if-station-set, else auto.
                if self._last_on_mode in ("auto", "manual"):
                    self._mode = self._last_on_mode
                else:
                    self._mode = "manual" if self._station else "auto"
            self._ensure_station_locked()
            self._playing = True
            self._airplay_needs_pairing = ""   # explicit gesture — allow a retry
            self._airplay_failed = ""
            # Playing again during the quiet window is an explicit override:
            # the auto-mode gate honours it until the window ends or stop().
            if self._in_quiet_hours():
                self._quiet_override = True
            if self._pi_side(self._output):
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
        # Re-verify the current station every 10 min — a persisted or
        # previously-picked mount can be dead (or die mid-listen).
        # _feed_ok trusts the current station, so this is the only
        # recovery path. Applies to manual too: when a hand-picked feed
        # dies we fall back to auto so the radio keeps working instead
        # of ERRing forever. The probe (a ~2s ranged GET) runs OUTSIDE
        # the lock — under it, it froze status()/display_state() and the
        # mirror's 2s poll; the verdict is applied under the lock below,
        # and only if the station is still the one that was probed.
        verify_code = ""
        with self._lock:
            if self._enabled and self._mode != "off" and self._station \
                    and (_now() - self._station_checked) > 600:
                self._station_checked = _now()
                verify_code = self._station
        verify_dead = bool(verify_code) and self._probe_feed(verify_code) is False
        with self._lock:
            if self._resume_pending:
                self._resume_pending = False
                if self._playing and self._pi_side(self._output):
                    ident = self._output.split(":", 1)[1] if ":" in self._output else ""
                    if self._output.startswith("airplay") and ident and \
                            ident in (self._airplay_needs_pairing, self._airplay_failed):
                        # A restored needs-pairing/failed AirPlay target must
                        # NOT relaunch on boot — that re-pops the receiver's
                        # code screen (or resumes hammering a dead feed).
                        # Stay stopped until an explicit start() or a
                        # successful pair clears the flag.
                        self._playing = False
                    else:
                        self._start_backend_locked()

            if not self._enabled or self._mode == "off":
                if self._playing:
                    self._stop_locked()
                return

            if verify_dead and self._station == verify_code:
                self._dead_feeds[verify_code] = _now()
                self._station = ""
                # Also stop a running Pi-side backend: if auto finds no
                # replacement below, it would otherwise keep playing and
                # fetching the dead mount forever.
                if self._playing and self._pi_side(self._output):
                    self._stop_backend_locked()
                if self._mode == "manual":
                    self._mode = "auto"

            # Supervise Pi-side backends: only AirPlay self-heals (its
            # reconnect loop); a crashed mpv or a dropped cast session
            # otherwise stranded playing:True over silence forever. Cheap
            # process/socket checks only — no network — and throttled so an
            # unreachable device isn't re-spawned on every 5s tick.
            if self._playing and self._station and self._pi_side(self._output):
                dead = False
                if self._output == "usb":
                    dead = (self._mpv_proc is not None
                            and self._mpv_proc.poll() is not None)
                elif self._output.startswith("chromecast"):
                    starting = (self._cast_thread is not None
                                and self._cast_thread.is_alive())
                    if not starting:
                        dev = self._cast_device
                        if dev is None:
                            dead = True   # start worker ended without a session
                        else:
                            sc = getattr(dev, "socket_client", None)
                            dead = (sc is not None
                                    and not getattr(sc, "is_connected", True))
                if dead and (_now() - self._backend_retry_ts) >= 30:
                    self._backend_retry_ts = _now()
                    self._start_backend_locked()

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
                        if self._playing and self._pi_side(self._output):
                            self._stop_backend_locked()
                            self._start_backend_locked()
                # Arm playback only when there is actually a station — no
                # point reporting "playing" silence when nothing resolved.
                if self._station and not self._playing:
                    self._playing = True
                    if self._pi_side(self._output):
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

    @staticmethod
    def _detect_usb_alsa_device():
        """mpv --audio-device string for the first USB-audio card (the ATC
        speaker), read from /proc/asound/cards.

        Prefers a shared software-mix PCM ('usbmix', a dmix defined in
        /etc/asound.conf) when configured, so the ATC stream and the hourly
        chime MIX instead of fighting over the exclusive hw device (the loser
        got 'Device or resource busy' -> silence). Falls back to plughw for the
        detected card when no dmix is set up. '' if there is no USB card (mpv
        then uses its own default)."""
        import re as _re
        try:
            with open("/proc/asound/cards") as f:
                text = f.read()
        except OSError:
            return ""
        card = None
        # " 2 [UACDemoV10     ]: USB-Audio - USB Audio Device"
        for m in _re.finditer(r"^\s*\d+\s*\[([^\]]+)\]:\s*(.+)$", text, _re.M):
            name, desc = m.group(1).strip(), m.group(2)
            if "USB-Audio" in desc or "USB Audio" in desc:
                card = name
                break
        if not card:
            return ""
        for cfg in ("/etc/asound.conf", os.path.expanduser("~/.asoundrc")):
            try:
                with open(cfg) as f:
                    if "pcm.usbmix" in f.read():
                        return "alsa/usbmix"
            except OSError:
                continue
        return f"alsa/plughw:CARD={card},DEV=0"

    def _start_mpv_locked(self):
        url = self._stream_url(self._station)
        if not url:
            return
        try:
            try:
                os.remove(self._MPV_IPC)
            except OSError:
                pass
            device = getattr(self, "_usb_device", "") or self._detect_usb_alsa_device()
            args = ["mpv", "--no-video", "--no-terminal", "--idle=no",
                    "--user-agent=" + _UA_HEADERS["User-Agent"],
                    f"--volume={self._volume}",
                    f"--input-ipc-server={self._MPV_IPC}"]
            if device:
                # Target the USB speaker explicitly; otherwise mpv plays to the
                # ALSA default (the Pi's onboard jack) and the USB output is mute.
                args.append(f"--audio-device={device}")
            args.append(url)
            self._mpv_proc = subprocess.Popen(
                args,
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
                    # terminate() didn't finish — SIGKILL, then wait() to REAP
                    # it. Without this second wait the killed mpv lingered as a
                    # <defunct> zombie until the next Popen happened to reap it.
                    self._mpv_proc.kill()
                    try:
                        self._mpv_proc.wait(timeout=2)
                    except Exception:
                        pass
            except Exception:
                pass
            self._mpv_proc = None
        try:
            os.remove(self._MPV_IPC)
        except OSError:
            pass

    # ── Backend: Chromecast (cast pulls the URL itself) ──────────────────
    @staticmethod
    def _cast_teardown(dev):
        """Stop/quit/disconnect a cast device, each step guarded on its own —
        one raising (e.g. stop() on a dead socket) must not skip the rest
        and leak the socket-client threads behind it."""
        if dev is None:
            return
        try:
            dev.media_controller.stop()
        except Exception:
            pass
        try:
            dev.quit_app()
        except Exception:
            pass
        try:
            dev.disconnect()
        except Exception:
            pass

    def _start_cast_locked(self):
        """Tell the selected cast device to pull the stream itself — from the
        Pi's relay when reachable (browser UA + one upstream connection; cast
        UAs fetching LiveATC from the house IP are ban-bait), falling back to
        the direct LiveATC URL. No Pi audio pipeline involved (review note 5).
        All pychromecast I/O (discovery + wait + quit_app + block_until_active
        ≈ 20s worst case) runs on a worker thread: doing it inline here held
        self._lock for the duration, freezing status()/display_state() and
        the mirror's 2s poll. We hold the lock, so snapshot everything the
        worker needs NOW; the worker must never touch self._lock — the
        stopper joins it while holding the lock."""
        url = self._stream_url(self._station)
        uuid = self._output.split(":", 1)[1] if ":" in self._output else ""
        if not url or not uuid:
            return
        try:
            import pychromecast  # noqa: F401
        except Exception:
            return
        station = self._station
        volume = self._volume
        output = self._output
        label = self._station_label()   # 'now playing' shown on the receiver
        outputs_cache = list(self._outputs_cache or [])
        stop_evt = threading.Event()
        self._cast_stop = stop_evt

        def _run():
            import pychromecast
            import uuid as _uuid_mod
            dev, browser = None, None
            try:
                # pychromecast matches UUID objects, not strings — a string
                # uuid silently finds nothing (casts were never commanded).
                # Try the UUID object first, then fall back to the friendly
                # name from the outputs cache.
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
                    browser = None
                    name = next((o.get("name") for o in outputs_cache
                                 if o.get("id") == output), None)
                    if not name:
                        return
                    casts, browser = pychromecast.get_listed_chromecasts(
                        friendly_names=[name])
                dev = casts[0] if casts else None
                if dev is None or stop_evt.is_set():
                    return
                dev.wait(timeout=5)
                # If another app owns the device (e.g. Pandora), media commands
                # land on ITS session instead of launching ours — quit it first.
                try:
                    if dev.status and dev.status.display_name and \
                            dev.status.display_name not in ("Backdrop", None, ""):
                        dev.quit_app()
                        if stop_evt.wait(3):   # settle wait, abandonable
                            return
                except Exception:
                    pass
                if stop_evt.is_set():
                    return
                dev.set_volume(volume / 100.0)
                mc = dev.media_controller
                # Prefer the relay: UDP-connect toward the cast device to learn
                # which of OUR addresses is on its LAN (exit nodes make the
                # default-route trick return the tailnet IP, which casts can't
                # reach). ?fmt=mp3 = 128 kbps re-encode so the receiver's startup
                # buffer fills in seconds (the 16 kbps source takes ~a minute).
                play_url = url
                try:
                    import socket as _sock
                    host = dev.cast_info.host
                    s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
                    try:
                        s.connect((host, 9))
                        my_ip = s.getsockname()[0]
                    finally:
                        s.close()
                    play_url = (f"http://{my_ip}:8080/atc/relay"
                                f"?code={station}&fmt=mp3")
                except Exception:
                    pass   # direct LiveATC URL stays as the fallback
                # "Now playing" label so the cast device / Google Home shows
                # what it's tuned to instead of a bare URL. STREAM_TYPE_LIVE
                # tells the receiver there's no scrubbable timeline.
                try:
                    from pychromecast.controllers.media import STREAM_TYPE_LIVE
                    _stype = STREAM_TYPE_LIVE
                except Exception:
                    _stype = "LIVE"
                # play_media autoplays on load; an extra play() raises
                # "no session" whenever the load failed — don't call it.
                mc.play_media(play_url, "audio/mpeg", title=label,
                              stream_type=_stype,
                              metadata={"metadataType": 0, "title": label,
                                        "subtitle": "LiveATC"})
                mc.block_until_active(timeout=8)
                if stop_evt.is_set():
                    # The stopper gave up joining us — nobody else holds this
                    # handle, so tear down our own session before exiting.
                    self._cast_teardown(dev)
                    dev = None
                    return
                # Publish the live handle. Plain attribute assignment (atomic
                # in CPython) instead of taking self._lock: _stop_cast_locked
                # joins this thread WHILE holding the lock, so acquiring it
                # here could stall the whole manager for the join timeout.
                self._cast_device = dev
                if stop_evt.is_set():
                    # Raced a stop that ran just before the assignment landed:
                    # this session is ours to clean up either way (a newer
                    # worker may already have replaced the slot).
                    if self._cast_device is dev:
                        self._cast_device = None
                    self._cast_teardown(dev)
            except Exception as e:
                print(f"ATC cast: start failed: {type(e).__name__}: {e}",
                      flush=True)
                self._cast_teardown(dev)
            finally:
                # The zeroconf CastBrowser leaked its threads (one per retry)
                # whenever wait/play_media/block_until_active raised — stop it
                # on EVERY exit path.
                try:
                    if browser:
                        browser.stop_discovery()
                except Exception:
                    pass

        self._cast_thread = threading.Thread(target=_run, daemon=True,
                                             name="atc-cast-start")
        self._cast_thread.start()

    def _stop_cast_locked(self):
        # A still-starting worker checks this event at each checkpoint and
        # tears down its own session if we give up waiting for it below.
        if self._cast_stop is not None:
            self._cast_stop.set()
        t = self._cast_thread
        if t is not None:
            t.join(timeout=5)
        self._cast_stop = None
        self._cast_thread = None
        dev = self._cast_device
        self._cast_device = None
        self._cast_teardown(dev)

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
        label = self._station_label()   # 'now playing' shown on the receiver
        print(f"ATC airplay: begin ident={ident} url_ok={bool(url)}", flush=True)
        if not url or not ident:
            return
        # This receiver already 470'd unpaired, or its reconnect loop already
        # gave up (dead feed / unreachable). Don't relaunch — tick() calls us
        # again on every auto-tune station change, which would re-pop the code
        # screen or resume hammering LiveATC. Only an explicit start()/
        # select_output() or a successful pair clears the flag for a retry.
        if self._airplay_needs_pairing == ident:
            print(f"ATC airplay: {ident} blocked on pairing — skipping start",
                  flush=True)
            return
        if self._airplay_failed == ident:
            print(f"ATC airplay: {ident} gave up earlier — skipping start "
                  f"(press play to retry)", flush=True)
            return
        try:
            import pyatv  # noqa: F401
        except Exception as e:
            print(f"ATC airplay: pyatv import failed: {e}", flush=True)
            return
        stop_evt = threading.Event()
        self._airplay_stop = stop_evt

        def _run():
            print("ATC airplay: thread running", flush=True)
            import asyncio
            from pyatv import scan as atv_scan, connect as atv_connect

            async def _stream():
                loop = asyncio.get_event_loop()
                results = await atv_scan(loop, identifier=ident, timeout=5)
                if not results:
                    print(f"ATC airplay: device {ident} not found in scan", flush=True)
                    return
                conf = results[0]
                # Stored pairing credentials. AirPlay-2 receivers (e.g. macOS,
                # HomePod) require BOTH the AirPlay and RAOP protocols paired,
                # so creds are stored per-protocol {ident: {"AirPlay": ...,
                # "RAOP": ...}}. Old single-string entries = RAOP-only.
                stored = _load_json(_AIRPLAY_CREDS, {}).get(ident)
                if stored:
                    from pyatv.const import Protocol
                    pairs = stored.items() if isinstance(stored, dict) \
                        else [("RAOP", stored)]
                    for pname, cred in pairs:
                        try:
                            conf.set_credentials(Protocol[pname], cred)
                        except Exception:
                            pass
                atv = await atv_connect(conf, loop)
                try:
                    # Push our volume explicitly — some receivers default the
                    # RAOP session very low/muted.
                    try:
                        await atv.audio.set_volume(float(self._volume))
                    except Exception:
                        pass
                    # stream_url pulls/pushes the URL to the receiver; it returns
                    # when playback ends. We poll stop_evt to allow teardown.
                    # metadata = the 'now playing' title shown on the receiver.
                    try:
                        from pyatv.interface import MediaMetadata
                        md = MediaMetadata(title=label, artist="LiveATC")
                    except Exception:
                        md = None
                    task = asyncio.ensure_future(
                        atv.stream.stream_file(url, metadata=md))
                    # Apply live volume changes: the poll loop already runs
                    # every 0.25s, so watch self._volume and push it to the
                    # receiver when the slider moves (was only set once above).
                    _last_vol = float(self._volume)
                    while not stop_evt.is_set() and not task.done():
                        cur_vol = float(self._volume)
                        if cur_vol != _last_vol:
                            _last_vol = cur_vol
                            try:
                                await atv.audio.set_volume(cur_vol)
                            except Exception:
                                pass
                        await asyncio.sleep(0.25)
                    if task.done() and task.exception():
                        exc = task.exception()
                        print(f"ATC airplay stream error: {exc}", flush=True)
                        # Authorization failures mean the receiver needs
                        # pairing — retrying just re-triggers its code screen
                        # every 3s (a screen-takeover loop on macOS). Match
                        # the pyatv exception TYPES first (message wording
                        # varies by version and missed NoCredentialsError);
                        # 470 is the AirPlay auth-required HTTP status; the
                        # substring test stays as the fallback.
                        auth_types = _pyatv_auth_excs()
                        txt = str(exc).lower()
                        if (auth_types and isinstance(exc, auth_types)) \
                                or "authoriz" in txt or "authenticat" in txt \
                                or "470" in txt:
                            return "auth"
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

            # Reconnect loop: RAOP sessions end on their own (receiver-side
            # drops, stream hiccups) — a one-shot stream left 'playing: True'
            # with silence and no process. Reconnect until an explicit stop,
            # BUT with exponential backoff and a failure cap: every attempt
            # opens a LiveATC fetch through the relay, and a flat retry against
            # a dead feed is exactly what got this IP banned (the browser path
            # already has this policy). A session that actually ran (>=30s)
            # resets the backoff; repeated short/failed sessions back off and
            # eventually give up. Each iteration is guarded so a scan/connect
            # error can't kill the loop and strand playing:True over silence.
            BASE_DELAY, MAX_DELAY, MAX_FAILS = 3, 300, 12
            fails = 0
            attempt = 0
            while not stop_evt.is_set():
                t0 = time.monotonic()
                try:
                    verdict = asyncio.new_event_loop().run_until_complete(_stream())
                except Exception as e:
                    print(f"ATC airplay iteration error: "
                          f"{type(e).__name__}: {e}", flush=True)
                    verdict = None
                ran = time.monotonic() - t0
                if verdict == "auth":
                    print("ATC airplay: receiver requires pairing — not "
                          "retrying. Use 'pair airplay' on the config page.",
                          flush=True)
                    self._airplay_needs_pairing = ident
                    break
                if stop_evt.is_set():
                    break
                if ran >= 30:
                    fails = 0                       # real session — reset backoff
                    delay = BASE_DELAY
                else:
                    fails += 1
                    if fails >= MAX_FAILS:
                        print(f"ATC airplay: {fails} failed/short sessions in a "
                              f"row — giving up (dead feed or receiver "
                              f"unreachable). Press play to retry.", flush=True)
                        self._airplay_failed = ident
                        break
                    delay = min(BASE_DELAY * (2 ** (fails - 1)), MAX_DELAY)
                attempt += 1
                print(f"ATC airplay: reconnect #{attempt} in {delay}s "
                      f"(consecutive fails={fails})", flush=True)
                if stop_evt.wait(delay):
                    break

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
    # AirPlay-2 receivers (macOS, HomePod) advertise BOTH Protocol.AirPlay and
    # Protocol.RAOP with PairingRequirement.Mandatory; pairing only RAOP leaves
    # streaming unauthorized (error 470, endless re-prompt). We pair EVERY
    # mandatory protocol in sequence — each may show its own PIN — and store
    # credentials per-protocol {ident: {"AirPlay": ..., "RAOP": ...}}. Older
    # AirPlay-1 receivers (AirPort Express) advertise only RAOP, so the loop
    # collapses to the single-protocol case automatically.
    #
    # HTTP is stateless, so one async session is bridged across begin/finish
    # calls by a dedicated thread + events. When more than one protocol needs a
    # PIN, finish() reports {more: true} and the UI prompts again for the next.
    def airplay_pair_begin(self, output_id):
        ident = output_id.split(":", 1)[1] if ":" in output_id else output_id
        try:
            import pyatv  # noqa: F401
        except Exception:
            return {"ok": False, "error": "pyatv not installed"}
        self.airplay_pair_cancel()
        state = {"ident": ident, "stage": "starting", "error": "",
                 "protocol": "", "creds": {},
                 "pin_event": threading.Event(),   # caller -> thread
                 "step_event": threading.Event(),  # thread -> caller
                 "pin": None,
                 "done_event": threading.Event()}
        # Swap the session handle under the lock — two concurrent begin()
        # calls (double-click) otherwise interleave and strand a thread.
        with self._lock:
            self._airplay_pairing = state

        def _run():
            import asyncio
            from pyatv import scan as atv_scan, pair as atv_pair
            from pyatv.const import Protocol, PairingRequirement

            async def _pair_one(conf, proto, loop):
                """Pair a single protocol; may pause for a PIN. Returns creds
                string on success, raises on failure."""
                pairing = await atv_pair(conf, proto, loop)
                try:
                    await pairing.begin()
                    if pairing.device_provides_pin:
                        # PIN shows ON the receiver — hand control to the caller.
                        state["pin"] = None
                        state["pin_event"].clear()
                        state["protocol"] = proto.name
                        state["stage"] = "awaiting_pin"
                        state["step_event"].set()
                        ok = await loop.run_in_executor(
                            None, state["pin_event"].wait, 120)
                        if not ok:
                            raise RuntimeError(f"{proto.name} PIN timed out")
                        pairing.pin(state["pin"])
                    await pairing.finish()
                    if not pairing.has_paired:
                        raise RuntimeError(f"{proto.name} pairing not accepted")
                    return pairing.service.credentials
                finally:
                    try:
                        await pairing.close()
                    except Exception:
                        pass

            async def _flow():
                loop = asyncio.get_event_loop()
                results = await atv_scan(loop, identifier=ident, timeout=6)
                if not results:
                    state.update(stage="error", error="device not found")
                    return
                conf = results[0]
                # Collect the protocols this receiver actually requires. Order
                # RAOP last so the audio protocol's PIN is the final prompt.
                want = []
                for proto in (Protocol.AirPlay, Protocol.RAOP):
                    svc = conf.get_service(proto)
                    if svc is None:
                        continue
                    if svc.pairing in (PairingRequirement.Mandatory,
                                       PairingRequirement.Optional):
                        want.append(proto)
                if not want:
                    # Nothing to pair (open receiver) — streaming works as-is.
                    state["stage"] = "paired"
                    return
                creds = {}
                for proto in want:
                    creds[proto.name] = await _pair_one(conf, proto, loop)
                state["creds"] = creds
                state["stage"] = "paired"

            try:
                asyncio.new_event_loop().run_until_complete(_flow())
                if state["stage"] == "paired" and state["creds"]:
                    stored = _load_json(_AIRPLAY_CREDS, {})
                    stored[ident] = state["creds"]
                    # Owner-only from the moment the file appears (pairing
                    # SECRETS) — no world-readable window.
                    _atomic_write(_AIRPLAY_CREDS, stored, mode=0o600)
                    if self._airplay_needs_pairing == ident:
                        self._airplay_needs_pairing = ""
            except Exception as e:
                state.update(stage="error", error=str(e)[:120])
            finally:
                state["step_event"].set()
                state["done_event"].set()

        threading.Thread(target=_run, daemon=True, name="atc-airplay-pair").start()
        # Wait for the first checkpoint (a PIN prompt, completion, or error).
        state["step_event"].wait(12)
        state["step_event"].clear()
        return {"ok": state["stage"] != "error", "stage": state["stage"],
                "protocol": state["protocol"],
                "more": state["stage"] == "awaiting_pin",
                "error": state["error"]}

    def airplay_pair_status(self):
        """Current pairing-session state, for UI polling. Slow receivers
        (Apple TV: scan + AirPlay setup) can outlast begin()'s 12s HTTP wait —
        the session keeps going in its thread, and the UI polls this until it
        reaches awaiting_pin/paired/error."""
        with self._lock:
            state = getattr(self, "_airplay_pairing", None)
        if not state:
            return {"ok": False, "stage": "none", "error": "no pairing session"}
        resp = {"ok": state["stage"] != "error", "stage": state["stage"],
                "protocol": state["protocol"],
                "more": state["stage"] == "awaiting_pin",
                "error": state["error"]}
        # Session finished and reported: drop the handle so later
        # {"status": true} polls don't resurrect the old session forever.
        self._maybe_clear_pairing(state)
        return resp

    def _maybe_clear_pairing(self, state):
        """Clear a COMPLETED pairing session (paired or errored, thread done)
        after its final state has been reported once."""
        if state["done_event"].is_set() and state["stage"] in ("paired", "error"):
            with self._lock:
                if self._airplay_pairing is state:
                    self._airplay_pairing = None

    def airplay_pair_finish(self, pin):
        with self._lock:
            state = self._airplay_pairing
        if not state or state["stage"] != "awaiting_pin":
            return {"ok": False, "error": "no pairing awaiting a PIN"}
        state["pin"] = str(pin).strip()
        state["step_event"].clear()
        state["pin_event"].set()
        # Wait for the next checkpoint: another protocol's PIN, done, or error.
        if not state["step_event"].wait(30):
            # Timed out with the thread still inside pairing.finish(): the
            # stage still reads "awaiting_pin", but reporting that (with
            # more:true) makes the UI re-prompt for a PIN nobody asked for.
            # Return a neutral in-progress verdict; the UI can poll status.
            return {"ok": False, "stage": "working",
                    "protocol": state["protocol"], "more": False,
                    "error": "pairing still in progress"}
        more = state["stage"] == "awaiting_pin"
        resp = {"ok": state["stage"] in ("awaiting_pin", "paired"),
                "stage": state["stage"], "protocol": state["protocol"],
                "more": more, "error": state["error"]}
        self._maybe_clear_pairing(state)
        return resp

    def airplay_pair_cancel(self):
        with self._lock:
            state = getattr(self, "_airplay_pairing", None)
            self._airplay_pairing = None
        if state:
            state["pin_event"].set()
            # The thread may be mid pairing.finish() network round-trip —
            # give it a real chance to close the session (2s routinely left
            # orphaned sessions open on slow receivers like Apple TVs).
            state["done_event"].wait(10)
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
def _pyatv_auth_excs():
    """Best-effort tuple of pyatv auth-failure exception types. Matching on
    message substrings alone missed NoCredentialsError (its text says
    neither 'authoriz' nor 'authenticat'); exception names vary across
    pyatv versions, so each is looked up defensively."""
    excs = []
    try:
        from pyatv import exceptions as _pex
        for name in ("AuthenticationError", "NoCredentialsError"):
            e = getattr(_pex, name, None)
            if isinstance(e, type) and issubclass(e, BaseException):
                excs.append(e)
    except Exception:
        pass
    return tuple(excs)


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
