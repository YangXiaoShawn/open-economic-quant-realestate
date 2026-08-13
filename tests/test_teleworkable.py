"""Teleworkable-share adapter.

The value of this control depends entirely on it being (a) the measure we say it is and
(b) used in a way that can actually bind. These tests cover both, because getting either
wrong produces an artifact that claims a control it does not have.
"""

from __future__ import annotations

import pytest

from lockin.adapters.base import AdapterError
from lockin.adapters.teleworkable import (
    _FIPS_TO_USPS,
    DEFAULT_MEASURE,
    MEASURES,
    _parse_csv,
)

_HEADER = "AREA,STATE,teleworkable_manual_emp,teleworkable_manual_wage,teleworkable_emp,teleworkable_wage\n"


def _body(rows: str) -> bytes:
    return (_HEADER + rows).encode("utf-8")


def test_parses_stata_style_bare_decimals():
    """The published files write ``.2651`` with no leading zero."""
    rows = _parse_csv(_body("1,Alabama,.26517713,.35357139,.30571073,.38827246\n"), "STATE")
    assert len(rows) == 1
    assert rows[0]["teleworkable_manual_emp"] == pytest.approx(0.26517713)
    assert rows[0]["teleworkable_emp"] == pytest.approx(0.30571073)


def test_default_measure_is_the_survey_based_one_not_the_authors_opinion():
    """``manual`` is Dingel & Neiman's own subjective classification.

    The survey-derived rule is the reproducible one, so it is the default. If this ever
    flips, it must be a deliberate decision rather than an editing accident -- the whole
    point of the control is that a sceptical reader can rebuild it.
    """
    assert DEFAULT_MEASURE == "teleworkable_emp"
    assert "manual" not in DEFAULT_MEASURE


def test_all_four_published_measures_are_retained():
    """The three non-default measures exist so the choice can be varied, not asserted."""
    assert set(MEASURES) == {
        "teleworkable_emp",
        "teleworkable_wage",
        "teleworkable_manual_emp",
        "teleworkable_manual_wage",
    }


def test_missing_measure_column_raises_rather_than_falling_back():
    """An upstream layout change must stop the run, not silently drop a control."""
    bad = b"AREA,STATE,teleworkable_emp\n1,Alabama,.3057\n"
    with pytest.raises(AdapterError, match="missing expected measure"):
        _parse_csv(bad, "STATE")


def test_unparseable_row_is_dropped_not_zero_filled():
    """A blank measure must not become 0.0 -- that would look like a real low value."""
    rows = _parse_csv(
        _body("1,Alabama,.265,.353,.305,.388\n2,Alaska,,,,\n"),
        "STATE",
    )
    assert [r["area_code"] for r in rows] == [1]


def test_every_row_dropped_raises():
    with pytest.raises(AdapterError, match="zero usable rows"):
        _parse_csv(_body("1,Alabama,x,x,x,x\n"), "STATE")


def test_fips_map_covers_the_fifty_states_plus_dc():
    assert len(_FIPS_TO_USPS) == 52  # 50 states + DC + PR
    assert _FIPS_TO_USPS[11] == "DC"
    assert _FIPS_TO_USPS[72] == "PR"
    assert len(set(_FIPS_TO_USPS.values())) == len(_FIPS_TO_USPS)


def test_fips_map_has_no_placeholder_codes():
    """FIPS 3, 7, 14 and 43 are unassigned and must not appear."""
    for unassigned in (3, 7, 14, 43, 52):
        assert unassigned not in _FIPS_TO_USPS
