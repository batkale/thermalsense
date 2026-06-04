"""
OGN live glider feed via the official APRS TCP stream.
Pure-Python — no external libraries required.

Protocol: connect to aprs.glidernet.org:10152, send an APRS login line with
an 'r/' radius filter, then read newline-delimited APRS beacon strings.
"""
import re
import socket
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from config import LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, OGN_FILTER_RADIUS

# Every parsed beacon is appended here — the seed script reads it for training
_DB_PATH = Path("data/ogn_history.db")

def _init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_PATH) as con:
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
        # Purge any beacons outside the Europe/GB bounding box left from previous sessions
        con.execute(
            "DELETE FROM beacons WHERE lat < ? OR lat > ? OR lon < ? OR lon > ?",
            (LAT_MIN, LAT_MAX, LON_MIN, LON_MAX),
        )

def _log_beacon(beacon: dict) -> None:
    try:
        with sqlite3.connect(_DB_PATH) as con:
            con.execute(
                "INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    beacon["id"], beacon["lat"], beacon["lon"],
                    beacon["alt"], beacon["vario"],
                    int(beacon["circling"]), int(beacon["is_tow"]),
                )
            )
    except Exception:
        pass  # never let logging break the stream

_APRS_SERVER  = "aprs.glidernet.org"
_APRS_PORT    = 10152
_RECONNECT_S  = 15
_GLIDER_TTL_S = 300  # drop entries not refreshed within 5 minutes

_live_gliders: dict[str, dict] = {}
_lock = threading.Lock()

# Per-glider ring buffer of ALL beacons received since the last WebSocket drain.
# Keyed by glider id; each value is a list of {lat, lon, alt} dicts.
# This lets the WebSocket send every intermediate position, not just the latest snapshot,
# which is what makes thermalling spirals visible in the frontend.
_beacon_buffers: dict[str, list[dict]] = {}
_bbuf_lock = threading.Lock()

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

        # aircraft type: bits 5-2 of the OGN id-byte; 2 = tow plane
        is_tow = False
        if im := _ID_RE.search(payload):
            id_byte = int(im.group(1), 16)
            is_tow  = ((id_byte >> 2) & 0x0F) == 2

        return {
            "id":        callsign,
            "lat":       lat,
            "lon":       lon,
            "alt":       alt,
            "vario":     vario,
            "heading":   heading,
            "speed_kmh": speed_kmh,
            "circling":  abs(turn_rate) > 8,
            "is_tow":    is_tow,
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
                login = (
                    f"user N0CALL pass -1 vers ThermalSense 1.0 "
                    f"filter {_active_filter}\r\n"
                )
                s.sendall(login.encode("ascii"))
                print(f"[OGN APRS] connected — filter: {_active_filter}")

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
                            with _lock:
                                _live_gliders[beacon["id"]] = beacon | {"seen_at": time.monotonic()}
                            with _bbuf_lock:
                                bbuf = _beacon_buffers.setdefault(beacon["id"], [])
                                bbuf.append({"lat": beacon["lat"], "lon": beacon["lon"], "alt": beacon["alt"]})
                                if len(bbuf) > 600:
                                    _beacon_buffers[beacon["id"]] = bbuf[-600:]
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
    lat_c = (LAT_MIN + LAT_MAX) / 2
    lon_c = (LON_MIN + LON_MAX) / 2
    _active_filter = f"r/{lat_c:.4f}/{lon_c:.4f}/{OGN_FILTER_RADIUS}"
    threading.Thread(target=_stream_thread, daemon=True).start()
    print(f"[OGN APRS] stream starting — filter: {_active_filter}")



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


def drain_beacon_buffers() -> dict[str, list[dict]]:
    """Return all buffered positions received since the last call, then clear the buffer.

    The WebSocket calls this every frame so the frontend receives every intermediate
    position (not just the latest snapshot), which is what produces circular thermalling
    spirals instead of coarse zigzags.
    """
    with _bbuf_lock:
        out = {k: v[:] for k, v in _beacon_buffers.items() if v}
        for k in out:
            _beacon_buffers[k] = []
    return out


async def fetch_ogn_gliders() -> list[dict]:
    """Return a snapshot of all gliders currently in the active APRS filter region."""
    now = time.monotonic()
    with _lock:
        stale = [k for k, v in _live_gliders.items() if now - v["seen_at"] >= _GLIDER_TTL_S]
        for k in stale:
            del _live_gliders[k]
        return list(_live_gliders.values())
