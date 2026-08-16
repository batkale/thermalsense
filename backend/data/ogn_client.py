"""
OGN live glider feed via the official APRS TCP stream.
Pure-Python — no external libraries required.

Protocol: connect to aprs.glidernet.org on the filtered port, send an APRS login
line carrying an area filter, then read newline-delimited APRS beacon strings.
"""
import math
import re
import socket
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from config import (
    LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, WORLDWIDE, OGN_APRS_PORT, OGN_APRS_FILTER,
    BEACON_RETENTION_DAYS,
)
from config import DB_PATH as _DB_PATH

# Every parsed beacon is appended to _DB_PATH — the seed script reads it for training

def _init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_PATH) as con:
        # Must be the first statements on the connection: journal_mode cannot be
        # changed inside a transaction.  WAL persists in the file header; the
        # rollback journal it replaces cost several fsyncs per beacon, and
        # sqlite3 holds the GIL across them, freezing the event loop.
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS beacons (

                ts        TEXT NOT NULL,
                id        TEXT NOT NULL,
                lat       REAL NOT NULL,
                lon       REAL NOT NULL,
                alt       REAL NOT NULL,
                vario     REAL NOT NULL,
                circling  INTEGER NOT NULL,
                is_tow    INTEGER NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_ts ON beacons(ts)")
        # Composite, and the column order matters.  fetch_glider_track filters
        # "id = ? AND ts >= ?"; with only idx_ts, sqlite narrows on ts and then
        # scans every row in the window looking for the id — ~4M rows and 1.5s
        # per track on a worldwide feed, holding the GIL throughout, once per
        # glider on the map.  Leading with id makes it a direct range lookup.
        con.execute("CREATE INDEX IF NOT EXISTS idx_id_ts ON beacons(id, ts)")

        # Migration: rows written before aircraft-type filtering have no ac_type.
        # They stay NULL, which the training query treats as "unclassifiable" and
        # skips — those rows may contain jets, helicopters and ground receivers.
        cols = {r[1] for r in con.execute("PRAGMA table_info(beacons)")}
        if "ac_type" not in cols:
            con.execute("ALTER TABLE beacons ADD COLUMN ac_type INTEGER")

        # Migration: rows written before aerotow detection have no under_tow.
        # NULL means "never evaluated", not "not on tow" — those rows predate any
        # tow plane being admitted to the feed, so a towed climb in them is
        # indistinguishable from a thermal.  The training query skips them.
        if "under_tow" not in cols:
            con.execute("ALTER TABLE beacons ADD COLUMN under_tow INTEGER")

        # Purge beacons outside the configured box left over from a previous run
        # with different bounds.  Skipped when worldwide: it could match nothing
        # anyway, and a full scan of a multi-million-row table at every startup
        # is not free.  Widening the box therefore never deletes existing history.
        if not WORLDWIDE:
            con.execute(
                "DELETE FROM beacons WHERE lat < ? OR lat > ? OR lon < ? OR lon > ?",
                (LAT_MIN, LAT_MAX, LON_MIN, LON_MAX),
            )

# Beacon writes are batched.  One connection per beacon, each committing a single
# row, meant ~70 transactions/s against a multi-hundred-MB file; batching cuts that
# to roughly one every five seconds.  Only _stream_thread writes, so the connection
# needs no lock — but it must be opened lazily from that thread, since sqlite3
# rejects a connection used from a thread other than the one that created it.
_writer: sqlite3.Connection | None = None
_pending: list[tuple] = []
_last_flush  = 0.0
_FLUSH_ROWS  = 200
_FLUSH_SECS  = 5.0


def flush_beacons() -> None:
    """Write any queued beacons.  Safe to call from the stream thread or shutdown."""
    global _writer, _last_flush
    _last_flush = time.monotonic()
    if not _pending:
        return
    try:
        if _writer is None:
            _writer = sqlite3.connect(_DB_PATH)
            _writer.execute("PRAGMA synchronous=NORMAL")   # per-connection, unlike journal_mode
        # Named columns, not positional VALUES — the table gained ac_type and
        # a bare INSERT would silently shift every field on the next change.
        with _writer:                                      # one transaction per batch
            _writer.executemany(
                "INSERT INTO beacons"
                " (ts, id, lat, lon, alt, vario, circling, is_tow, ac_type, under_tow)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                _pending,
            )
    except Exception:
        # Drop the connection so the next flush rebuilds it; keeping a broken one
        # would silently swallow every beacon from here on.
        try:
            if _writer is not None:
                _writer.close()
        except Exception:
            pass
        _writer = None
    finally:
        # Cleared either way: a batch that cannot be written must not accumulate
        # until it exhausts memory.
        _pending.clear()


def purge_old_beacons(days: float | None = None, chunk: int = 50_000) -> int:
    """
    Delete beacons older than `days` (default BEACON_RETENTION_DAYS).  Returns
    the number of rows removed.

    Deleted in bounded chunks rather than as one statement: a single day of the
    worldwide feed is several million rows, and one big DELETE would hold a write
    transaction long enough to block the APRS writer behind it.  sqlite3 releases
    the GIL around its C calls, so call this from a worker thread — never inline
    on the event loop.
    """
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(days=BEACON_RETENTION_DAYS if days is None else days)
    ).isoformat()
    deleted = 0
    try:
        # timeout: the stream thread is writing concurrently, and WAL still
        # serialises writers.  Wait rather than raise "database is locked".
        with sqlite3.connect(_DB_PATH, timeout=30) as con:
            con.execute("PRAGMA synchronous=NORMAL")
            while True:
                cur = con.execute(
                    "DELETE FROM beacons WHERE ROWID IN"
                    " (SELECT ROWID FROM beacons WHERE ts < ? LIMIT ?)",
                    (cutoff, chunk),
                )
                con.commit()
                deleted += cur.rowcount
                if cur.rowcount < chunk:
                    break
    except Exception as exc:
        print(f"[OGN] beacon purge failed: {exc}")
    return deleted


def _log_beacon(beacon: dict) -> None:
    """Queue a beacon for the next batch write.  Stream thread only."""
    _pending.append((
        datetime.now(timezone.utc).isoformat(),
        beacon["id"], beacon["lat"], beacon["lon"],
        beacon["alt"], beacon["vario"],
        int(beacon["circling"]), int(beacon["is_tow"]),
        beacon.get("ac_type"),
        int(beacon.get("under_tow", False)),
    ))
    if len(_pending) >= _FLUSH_ROWS or time.monotonic() - _last_flush >= _FLUSH_SECS:
        flush_beacons()

_APRS_SERVER  = "aprs.glidernet.org"
_APRS_PORT    = OGN_APRS_PORT
_RECONNECT_S  = 15
_GLIDER_TTL_S = 300  # drop entries not refreshed within 5 minutes

_live_gliders: dict[str, dict] = {}
_lock = threading.Lock()

# Per-glider ring buffer of ALL beacons received since the last WebSocket drain.
# Keyed by glider id; each value is a list of {lat, lon, alt} dicts.
# This lets the WebSocket send every intermediate position, not just the latest snapshot,
# which is what makes thermalling spirals visible in the frontend.
#
# Only the WebSocket drains this, so with no browser attached every glider would
# fill to _BBUF_MAX and stay there — hundreds of MB across a worldwide feed.
# When nothing has drained recently we keep only a short tail; a client that
# connects later backfills its track from SQLite via fetch_glider_track anyway.
_BBUF_MAX          = 600   # positions per glider while a client is consuming
_BBUF_IDLE_MAX     = 20    # positions per glider when nothing is consuming
_BBUF_IDLE_AFTER_S = 30    # no drain within this window => assume no consumer

_beacon_buffers: dict[str, list[dict]] = {}
_bbuf_lock = threading.Lock()
_last_drain = 0.0

# Monotonic counter stamped onto every buffered position.  It is what lets each
# subscriber keep its own read cursor instead of consuming the buffer, so N
# viewers all receive the same beacons (see drain_beacon_buffers).
_bbuf_seq = 0


def _buffer_position(beacon: dict) -> None:
    """Append a position to the glider's replay buffer.  Stream thread only."""
    global _bbuf_seq
    with _bbuf_lock:
        _bbuf_seq += 1
        bbuf = _beacon_buffers.setdefault(beacon["id"], [])
        bbuf.append({"lat": beacon["lat"], "lon": beacon["lon"],
                     "alt": beacon["alt"], "seq": _bbuf_seq})
        cap = (
            _BBUF_MAX
            if time.monotonic() - _last_drain < _BBUF_IDLE_AFTER_S
            else _BBUF_IDLE_MAX
        )
        if len(bbuf) > cap:
            _beacon_buffers[beacon["id"]] = bbuf[-cap:]

_active_filter = ""
_active_socket: socket.socket | None = None
_socket_lock   = threading.Lock()

# ---------------------------------------------------------------------------
# Regex patterns for the OGN uncompressed APRS position format:
#   FLRxxxxxx>APRS,...:/HHMMSSh DDMMssN/DDDMMssE'CCC/SSS/A=FFFFFF !WCC!
#                               id3Axxxxxx +VVVfpm +T.Trot ...
# ---------------------------------------------------------------------------
_POS_RE   = re.compile(
    r'(\d{2})(\d{2}\.\d+)([NS])'   # lat: degrees, decimal-minutes, hemisphere
    r'[/\\]'                         # APRS symbol-table identifier
    r'(\d{3})(\d{2}\.\d+)([EW])'   # lon: degrees, decimal-minutes, hemisphere
)
_ALT_RE    = re.compile(r'/A=(\d+)')                    # altitude in feet
_VARIO_RE  = re.compile(r'([+-]\d+)fpm')               # vertical speed in ft/min
_ROT_RE    = re.compile(r'([+-]\d+(?:\.\d+)?)rot')     # turn rate in half-turns/min
_COURSE_RE = re.compile(r'[EW][^\s/\\](\d{3})/(\d{3})') # symbol-char + course(deg)/speed(kts)
_ID_RE     = re.compile(r'\bid([0-9A-Fa-f]{2})[0-9A-Fa-f]{6}')  # OGN aircraft-type byte

# --- Aircraft types (OGN id-byte, bits 5-2) ----------------------------------
AC_TYPE_NAMES = {
    0x0: "unknown",       0x1: "glider",          0x2: "tow plane",
    0x3: "helicopter",    0x4: "skydiver",        0x5: "drop plane",
    0x6: "hang glider",   0x7: "paraglider",      0x8: "powered aircraft",
    0x9: "jet aircraft",  0xA: "ufo",             0xB: "balloon",
    0xC: "airship",       0xD: "uav",             0xE: "reserved",
    0xF: "static object",
}

# Types that climb under engine power, or are not aircraft at all.  The OGN feed
# is ~53% these — a jet in a holding pattern circling with positive vario is a
# textbook false "confirmed thermal".
EXCLUDED_AC_TYPES = frozenset({
    0x3,  # helicopter
    0x8,  # powered aircraft
    0x9,  # jet aircraft
    0xB,  # balloon
    0xD,  # uav / drone
    0xF,  # static object — a ground receiver, not an aircraft
})

# Aircraft that gain height by soaring.  A circling one is genuine lift evidence,
# so only these may produce a positive thermal label.  Paragliders and hang
# gliders count: they are slower than sailplanes and core a thermal tightly.
SOARING_AC_TYPES = frozenset({
    0x1,  # glider / motor glider
    0x6,  # hang glider
    0x7,  # paraglider
})

# The only types allowed past the parse boundary — an allow-list, not a deny-list.
#
# A deny-list leaked badly.  OGN relays ADS-B traffic (tocall OGADSB) alongside
# FLARM, and airliners arrive either with no id field at all (ac_type None) or
# with the type nibble set to 0x0 "unknown".  Neither is in EXCLUDED_AC_TYPES, so
# every airliner parked on an apron or holding overhead was admitted and drawn as
# a glider — Madrid Barajas rendered ~30 "gliders" sitting at its gates.
#
# Anything we cannot positively identify is therefore dropped here, so it never
# reaches the map, the beacon DB, thermal clustering or the training labels.
# New OGN type codes and new relay sources fail closed.
TOW_AC_TYPE = 0x2

# Tow planes are admitted but are NOT in SOARING_AC_TYPES, so they can never
# confirm lift themselves.  They earn their place by marking the gliders behind
# them: a glider on aerotow climbs at 2-3 m/s and its tug will happily circle in
# a thermal during the climb, which is indistinguishable from thermalling unless
# the tug is visible.  Without them, every aerotow was a false positive.
DISPLAY_AC_TYPES = SOARING_AC_TYPES | {TOW_AC_TYPE}


def is_soaring(beacon: dict) -> bool:
    """True when a beacon came from an aircraft that climbs on lift alone."""
    return beacon.get("ac_type") in SOARING_AC_TYPES


def is_thermal_evidence(beacon: dict) -> bool:
    """True when this beacon's climb may be attributed to a thermal.

    Soaring type, and not currently on the end of a rope.  Every consumer that
    turns a climb into lift — map fusion, thermal clustering, training labels —
    must use this rather than is_soaring alone.
    """
    return is_soaring(beacon) and not beacon.get("under_tow", False)


# --- Aerotow detection --------------------------------------------------------
# A glider under tow sits ~60 m behind the tug on the rope at matched altitude.
# The thresholds are looser than that because OGN beacons are not synchronised:
# each aircraft reports every few seconds, so at a 110 km/h tow speed the two
# positions can be several hundred metres apart purely from reporting skew.
_TOW_RADIUS_M   = 400.0   # horizontal separation
_TOW_ALT_DIFF_M = 100.0   # vertical separation
_TOW_MAX_AGE_S  = 15.0    # ignore tugs whose last beacon is stale
_TOW_MIN_VARIO  = 0.3     # a tug parked or descending is not towing anyone


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp     = p2 - p1
    dl     = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(a))


def _under_tow(beacon: dict, live: dict[str, dict], now: float) -> bool:
    """True when a climbing tow plane is close enough to be towing this aircraft."""
    if not is_soaring(beacon):
        return False   # a tug is not "under tow"; nor is anything we don't track
    for other in live.values():
        if other.get("ac_type") != TOW_AC_TYPE:
            continue
        if now - other.get("seen_at", 0.0) > _TOW_MAX_AGE_S:
            continue
        if other.get("vario", 0.0) < _TOW_MIN_VARIO:
            continue
        if abs(other.get("alt", 0) - beacon["alt"]) > _TOW_ALT_DIFF_M:
            continue
        if _distance_m(beacon["lat"], beacon["lon"],
                       other["lat"], other["lon"]) <= _TOW_RADIUS_M:
            return True
    return False


def _parse_beacon(line: str) -> dict | None:
    """Parse one raw APRS line.  Returns a glider dict or None."""
    if not line or line.startswith('#') or '>' not in line or ':' not in line:
        return None
    try:
        callsign = line.split('>')[0].strip()
        payload  = line.split(':', 1)[1]

        # Only handle uncompressed position reports
        if not payload or payload[0] not in '/@':
            return None

        m = _POS_RE.search(payload)
        if not m:
            return None

        lat = int(m.group(1)) + float(m.group(2)) / 60
        if m.group(3) == 'S':
            lat = -lat
        lon = int(m.group(4)) + float(m.group(5)) / 60
        if m.group(6) == 'W':
            lon = -lon

        alt = 0
        if am := _ALT_RE.search(payload):
            alt = round(int(am.group(1)) * 0.3048)  # ft → m

        vario = 0.0
        if vm := _VARIO_RE.search(payload):
            vario = round(int(vm.group(1)) * 0.00508, 2)  # fpm → m/s

        # rot is half-turns/min; ×3 converts to deg/s
        turn_rate = 0.0
        if rm := _ROT_RE.search(payload):
            turn_rate = float(rm.group(1)) * 3.0

        # course + speed: course 0 means unknown/stationary in APRS
        heading   = None
        speed_kmh = 0
        if cm := _COURSE_RE.search(payload):
            raw_course = int(cm.group(1))
            heading    = raw_course if raw_course > 0 else None
            speed_kmh  = round(int(cm.group(2)) * 1.852)  # knots → km/h

        # aircraft type: bits 5-2 of the OGN id-byte.  A beacon with no id field
        # cannot be classified, and unclassifiable traffic is not admitted.
        if not (im := _ID_RE.search(payload)):
            return None
        ac_type = (int(im.group(1), 16) >> 2) & 0x0F
        if ac_type not in DISPLAY_AC_TYPES:
            return None   # powered / unknown / non-aircraft — never enters the system

        return {
            "id":        callsign,
            "lat":       lat,
            "lon":       lon,
            "alt":       alt,
            "vario":     vario,
            "heading":   heading,
            "speed_kmh": speed_kmh,
            "circling":  abs(turn_rate) > 8,
            "ac_type":   ac_type,
            # Direct index, not .get(..., "unknown"): ac_type has already been
            # checked against DISPLAY_AC_TYPES, so a missing key would be a bug
            # worth raising rather than papering over with an "unknown" label.
            "ac_type_name": AC_TYPE_NAMES[ac_type],
            # is_tow: this aircraft IS a tug.  under_tow: this aircraft is being
            # towed BY one — resolved in the stream loop, which is the only place
            # with a view of the other traffic.  Two different questions.
            "is_tow":    ac_type == TOW_AC_TYPE,
            "under_tow": False,
        }
    except Exception:
        return None


def _stream_thread() -> None:
    """Persistent APRS TCP loop — reconnects automatically on any error."""
    global _active_socket
    while True:
        try:
            with socket.create_connection((_APRS_SERVER, _APRS_PORT), timeout=60) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                s.settimeout(10)  # wake up before the server's ~15 s idle close
                with _socket_lock:
                    _active_socket = s
                # A trailing bare "filter" keyword with no expression is a syntax
                # error to aprsc, so omit it entirely on the unfiltered full feed.
                login = "user N0CALL pass -1 vers ThermalSense 1.0"
                if _active_filter:
                    login += f" filter {_active_filter}"
                s.sendall((login + "\r\n").encode("ascii"))
                print(f"[OGN APRS] connected — {_active_filter or 'full feed (worldwide)'}")

                buf = b""
                while True:
                    try:
                        chunk = s.recv(4096)
                    except (TimeoutError, socket.timeout):
                        s.sendall(b"#keepalive\r\n")
                        continue
                    if not chunk:
                        raise ConnectionResetError("server closed connection")
                    buf += chunk
                    while b"\r\n" in buf:
                        raw, buf = buf.split(b"\r\n", 1)
                        beacon = _parse_beacon(raw.decode("ascii", errors="replace"))
                        if beacon and (LAT_MIN <= beacon["lat"] <= LAT_MAX and LON_MIN <= beacon["lon"] <= LON_MAX):
                            now = time.monotonic()
                            with _lock:
                                # Resolved here, not in _parse_beacon: deciding
                                # whether this aircraft is on a rope needs the
                                # other traffic, which only the live map has.
                                beacon["under_tow"] = _under_tow(beacon, _live_gliders, now)
                                _live_gliders[beacon["id"]] = beacon | {"seen_at": now}
                            _buffer_position(beacon)
                            _log_beacon(beacon)

        except Exception as exc:
            with _socket_lock:
                _active_socket = None
            if isinstance(exc, ConnectionResetError):
                print(f"[OGN APRS] server rotated connection — reconnecting in {_RECONNECT_S}s")
            else:
                print(f"[OGN APRS] {exc} — reconnecting in {_RECONNECT_S}s")
            time.sleep(_RECONNECT_S)


def start_ogn_stream() -> None:
    """Start the background APRS thread.  Call once at app startup."""
    global _active_filter
    _init_db()
    _active_filter = OGN_APRS_FILTER
    threading.Thread(target=_stream_thread, daemon=True).start()
    print(f"[OGN APRS] stream starting — port {_APRS_PORT}, filter: {_active_filter}")



def fetch_glider_track(glider_id: str, max_gap_minutes: int = 5) -> list[dict]:
    """Return the current flight segment for a glider.

    Walks back through today's beacons and stops at the first gap longer than
    max_gap_minutes, which indicates a landing or the end of a previous flight.
    """
    if not _DB_PATH.exists():
        return []
    # Look back far enough to cover an early-morning launch
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=16)).isoformat()
    try:
        with sqlite3.connect(_DB_PATH) as con:
            rows = con.execute(
                "SELECT lat, lon, alt, ts FROM beacons WHERE id = ? AND ts >= ? ORDER BY ts",
                (glider_id, cutoff),
            ).fetchall()
        if not rows:
            return []
        gap = timedelta(minutes=max_gap_minutes)
        segment_start = 0
        for i in range(len(rows) - 1, 0, -1):
            if datetime.fromisoformat(rows[i][3]) - datetime.fromisoformat(rows[i - 1][3]) > gap:
                segment_start = i
                break
        return [{"lat": r[0], "lon": r[1], "alt": r[2]} for r in rows[segment_start:]]
    except Exception:
        return []


def beacon_cursor() -> int:
    """Sequence head — where a newly attached subscriber should start reading."""
    with _bbuf_lock:
        return _bbuf_seq


def drain_beacon_buffers(since: int) -> tuple[dict[str, list[dict]], int]:
    """Positions buffered after sequence `since`, plus the cursor to pass next time.

    The WebSocket calls this every frame so the frontend receives every intermediate
    position (not just the latest snapshot), which is what produces circular thermalling
    spirals instead of coarse zigzags.

    Deliberately non-destructive.  This used to clear the buffer, which made it a
    single-consumer queue: with two browsers attached each drain swallowed the
    positions the other had not read yet, so both viewers saw roughly half the
    beacons and both drew zigzags.  Nothing about that was visible on a single
    dev machine — it only appeared once the deploy had more than one viewer.
    Buffers stay bounded by _BBUF_MAX instead; a subscriber that falls further
    behind than that window misses the overflow and backfills from SQLite via
    fetch_glider_track, which is the same guarantee it had before.
    """
    global _last_drain
    with _bbuf_lock:
        _last_drain = time.monotonic()
        head = _bbuf_seq
        out = {}
        for gid, positions in _beacon_buffers.items():
            # Append-ordered by seq, so the unread tail is contiguous: walk back
            # from the end rather than scanning the whole buffer.  At ~2200
            # gliders x 600 buffered positions a full scan would be 1.3M
            # comparisons per viewer per frame, for the handful that are new.
            i = len(positions)
            while i > 0 and positions[i - 1]["seq"] > since:
                i -= 1
            if i < len(positions):
                out[gid] = positions[i:]
    return out, head


async def fetch_ogn_gliders() -> list[dict]:
    """Return a snapshot of all gliders currently in the active APRS filter region."""
    now = time.monotonic()
    with _lock:
        stale = [k for k, v in _live_gliders.items() if now - v["seen_at"] >= _GLIDER_TTL_S]
        for k in stale:
            del _live_gliders[k]
        snapshot = list(_live_gliders.values())

    # Drop the position buffers too, else the dict keeps a key for every glider
    # ever seen.  Done outside _lock to keep the two locks unnested.
    if stale:
        with _bbuf_lock:
            for k in stale:
                _beacon_buffers.pop(k, None)
    return snapshot
