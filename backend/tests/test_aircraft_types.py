import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from data.ogn_client import (
    _parse_beacon, is_soaring, EXCLUDED_AC_TYPES, SOARING_AC_TYPES, AC_TYPE_NAMES,
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


def test_tow_plane_excluded_because_it_climbs_under_power():
    assert _parse_beacon(_line(0x2)) is None


def test_unclassified_beacon_is_kept_but_not_soaring():
    """No id field: we can't classify it, so it may show but must not confirm lift."""
    line = "FLR999999>APRS,qAS,TEST:/120000h5130.00N/00100.00W'090/080/A=003000 +200fpm"
    b = _parse_beacon(line)
    assert b is not None
    assert b["ac_type"] is None
    assert not is_soaring(b)


def test_is_soaring_rejects_missing_and_unknown_types():
    assert not is_soaring({})
    assert not is_soaring({"ac_type": None})
    assert not is_soaring({"ac_type": 0x0})   # "unknown" is not evidence of lift


def test_circling_flag_still_parses_for_soaring_aircraft():
    b = _parse_beacon(_line(0x1, rot="+4.0"))   # 4 half-turns/min -> 12 deg/s
    assert b["circling"] is True
    assert b["vario"] == pytest.approx(200 * 0.00508, abs=0.01)
