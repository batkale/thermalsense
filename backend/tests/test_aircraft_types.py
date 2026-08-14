import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from data.ogn_client import (
    _parse_beacon, is_soaring, EXCLUDED_AC_TYPES, SOARING_AC_TYPES, AC_TYPE_NAMES,
    DISPLAY_AC_TYPES, TOW_AC_TYPE, is_thermal_evidence, _under_tow,
)


def _line(ac_type: int, call: str = "FLR123456", rot: str = "+0.0") -> str:
    """One OGN APRS position report carrying the given aircraft type."""
    id_byte = (ac_type << 2) | 0x01          # type in bits 5-2, address type in 1-0
    return (
        f"{call}>APRS,qAS,TEST:/120000h5130.00N/00100.00W'090/080/A=003000 "
        f"id{id_byte:02X}ABCDEF +200fpm {rot}rot"
    )


@pytest.mark.parametrize("ac_type", sorted(EXCLUDED_AC_TYPES))
def test_powered_and_non_aircraft_are_dropped(ac_type):
    """These climb under power or aren't aircraft — they must never enter the system."""
    assert _parse_beacon(_line(ac_type)) is None, AC_TYPE_NAMES[ac_type]


@pytest.mark.parametrize("ac_type", sorted(SOARING_AC_TYPES))
def test_soaring_aircraft_are_kept_and_flagged(ac_type):
    b = _parse_beacon(_line(ac_type))
    assert b is not None
    assert b["ac_type"] == ac_type
    assert is_soaring(b)


def test_jet_is_excluded_and_glider_is_not():
    assert 0x9 in EXCLUDED_AC_TYPES          # jet aircraft — 33% of the raw feed
    assert 0x1 in SOARING_AC_TYPES           # glider
    assert not (EXCLUDED_AC_TYPES & SOARING_AC_TYPES)


def test_tow_plane_is_admitted_but_excluded_from_lift():
    """Tugs are kept deliberately, reversing an earlier decision to drop them.

    They climb under power, so they can never be lift evidence themselves.  But
    dropping them made aerotows invisible, and a glider on a rope climbing at
    2-3 m/s then looked exactly like a glider in a thermal.
    """
    assert TOW_AC_TYPE not in EXCLUDED_AC_TYPES
    assert TOW_AC_TYPE not in SOARING_AC_TYPES
    assert TOW_AC_TYPE in DISPLAY_AC_TYPES
    b = _parse_beacon(_line(TOW_AC_TYPE))
    assert b is not None and not is_thermal_evidence(b)


def test_unclassified_beacon_is_dropped():
    """No id field: we can't classify it, so it is not admitted at all.

    Previously these were kept and drawn on the map.  That is how airliners got
    through — see test_airliners_over_madrid_are_dropped.
    """
    line = "FLR999999>APRS,qAS,TEST:/120000h5130.00N/00100.00W'090/080/A=003000 +200fpm"
    assert _parse_beacon(line) is None


def test_unknown_type_is_dropped():
    """0x0 'unknown' is not evidence of a glider — OGN's ADS-B relay uses it heavily."""
    assert 0x0 not in DISPLAY_AC_TYPES
    assert _parse_beacon(_line(0x0)) is None


def test_airliners_over_madrid_are_dropped():
    """Regression: real beacons captured over Madrid Barajas, drawn as gliders.

    ICA34xxxx is an ICAO 24-bit address in the Spanish block, relayed by OGN's
    ADS-B gateway (tocall OGADSB).  The id-byte type nibble is 0x0 for the apron
    traffic and 0x9 (jet) for the airborne traffic; neither may reach the map.
    """
    apron = (
        "ICA342349>OGADSB,qAS,LEMD:/145334h4028.30N/00333.60W^000/000/A=001998 "
        "!W00! id01342349 +000fpm FL000.00 A0:IBE32PW"
    )
    airborne = (
        "ICA34234E>OGADSB,qAS,LEMD:/145334h4028.90N/00334.10W^070/360/A=013901 "
        "!W09! id2534234E -2176fpm FL130.49 A3:VLG79RM"
    )
    assert _parse_beacon(apron) is None
    assert _parse_beacon(airborne) is None


def test_adsb_equipped_glider_still_admitted():
    """An ICAO-addressed sailplane on the FLARM network is a real glider — keep it."""
    line = (
        "ICA4B4A79>OGFLR,qAS,PizNair:/145332h4649.91N/01003.84E'252/054/A=013174 "
        "!W97! id054B4A79 +395fpm +3.0rot 6.2dB -4.8kHz gps1x2"
    )
    b = _parse_beacon(line)
    assert b is not None
    assert b["ac_type"] == 0x1
    assert is_soaring(b)


def test_display_set_admits_only_soaring_types_and_tugs():
    """Fail-closed: only soaring types plus tow planes may be admitted."""
    assert DISPLAY_AC_TYPES == SOARING_AC_TYPES | {TOW_AC_TYPE}
    for t in range(0x10):
        admitted = _parse_beacon(_line(t)) is not None
        assert admitted == (t in DISPLAY_AC_TYPES), AC_TYPE_NAMES[t]


# --- aerotow ------------------------------------------------------------------

def _ac(ac_type, lat, lon, alt, vario=1.0, seen_at=1000.0):
    return {"id": f"X{ac_type}{lat}", "ac_type": ac_type, "lat": lat, "lon": lon,
            "alt": alt, "vario": vario, "seen_at": seen_at}


def test_tow_plane_admitted_and_flagged_but_never_soaring():
    """A tug must reach the map — it is how we spot a tow — yet never confirm lift."""
    b = _parse_beacon(_line(TOW_AC_TYPE))
    assert b is not None
    assert b["is_tow"] is True
    assert not is_soaring(b)
    assert not is_thermal_evidence(b)


def test_glider_is_not_flagged_as_a_tug():
    b = _parse_beacon(_line(0x1))
    assert b["is_tow"] is False
    assert b["under_tow"] is False


def test_glider_behind_a_climbing_tug_is_under_tow():
    glider = _ac(0x1, 51.0000, -1.0000, 500)
    live   = {"tug": _ac(TOW_AC_TYPE, 51.0010, -1.0000, 520, vario=2.5)}
    assert _under_tow(glider, live, now=1000.0)


def test_under_tow_suppresses_thermal_evidence():
    """The whole point: a towed climb must not read as lift."""
    glider = _parse_beacon(_line(0x1, rot="+4.0")) | {"under_tow": True}
    assert is_soaring(glider)            # still a glider
    assert not is_thermal_evidence(glider)  # but its climb is not a thermal


def test_distant_or_descending_tug_does_not_imply_tow():
    glider = _ac(0x1, 51.0, -1.0, 500)
    far     = {"t": _ac(TOW_AC_TYPE, 51.05, -1.0, 500, vario=2.5)}   # ~5.5 km away
    low     = {"t": _ac(TOW_AC_TYPE, 51.0, -1.0, 900, vario=2.5)}    # 400 m above
    sinking = {"t": _ac(TOW_AC_TYPE, 51.0, -1.0, 500, vario=-1.0)}   # not climbing
    stale   = {"t": _ac(TOW_AC_TYPE, 51.0, -1.0, 500, vario=2.5, seen_at=900.0)}
    for name, live in [("far", far), ("low", low), ("sinking", sinking), ("stale", stale)]:
        assert not _under_tow(glider, live, now=1000.0), name


def test_a_tug_is_never_itself_under_tow():
    tug   = _ac(TOW_AC_TYPE, 51.0, -1.0, 500, vario=2.5)
    live  = {"other": _ac(TOW_AC_TYPE, 51.0001, -1.0, 505, vario=2.5)}
    assert not _under_tow(tug, live, now=1000.0)


def test_lone_glider_with_no_tug_nearby_is_free():
    glider = _ac(0x1, 51.0, -1.0, 1500, vario=2.0)
    live   = {"g2": _ac(0x1, 51.0002, -1.0, 1505, vario=2.2)}  # gaggle, not a tow
    assert not _under_tow(glider, live, now=1000.0)


def test_is_soaring_rejects_missing_and_unknown_types():
    """is_soaring stays strict independently of the parse filter — defence in depth."""
    assert not is_soaring({})
    assert not is_soaring({"ac_type": None})
    assert not is_soaring({"ac_type": 0x0})   # "unknown" is not evidence of lift


def test_circling_flag_still_parses_for_soaring_aircraft():
    b = _parse_beacon(_line(0x1, rot="+4.0"))   # 4 half-turns/min -> 12 deg/s
    assert b["circling"] is True
    assert b["vario"] == pytest.approx(200 * 0.00508, abs=0.01)
