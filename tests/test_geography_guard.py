"""Geography-level compatibility in the panel builder.

A left join between MSA-keyed exposure and state-keyed outcomes does not fail. It
produces a panel that looks well-formed at the wrong level, which is how the first
MSA attempt in this repository yielded a plausible 182-row panel with exposure at 102
MSAs, outcomes at 26 states, and a control matching 0 rows -- silently.
"""

from __future__ import annotations

import polars as pl
import pytest

from lockin.config import load_config
from lockin.panel.build import GeographyMismatchError, _assert_geography_compatible


def _df(geos: list[str]) -> pl.DataFrame:
    return pl.DataFrame({"geography": geos, "value": [1.0] * len(geos)})


def test_disjoint_geography_keys_raise():
    cfg = load_config("configs/sample.yaml")
    with pytest.raises(GeographyMismatchError, match="shares NO values"):
        _assert_geography_compatible("Census BPS", _df(["AL", "AK"]), {"10180", "10420"}, cfg, [])


def test_error_names_both_key_shapes_so_the_fix_is_obvious():
    cfg = load_config("configs/sample.yaml")
    with pytest.raises(GeographyMismatchError) as exc:
        _assert_geography_compatible("HMDA", _df(["AL"]), {"10180"}, cfg, [])
    msg = str(exc.value)
    assert "AL" in msg and "10180" in msg


def test_partial_overlap_is_recorded_not_raised():
    """Genuine partial coverage is normal -- not every geography has every series."""
    cfg = load_config("configs/sample.yaml")
    notes: list[str] = []
    _assert_geography_compatible("FHFA HPI", _df(["CA", "TX"]), {"CA", "TX", "WY"}, cfg, notes)
    assert notes and "no FHFA HPI coverage" in notes[0]


def test_full_overlap_is_silent():
    cfg = load_config("configs/sample.yaml")
    notes: list[str] = []
    _assert_geography_compatible("FHFA HPI", _df(["CA", "TX"]), {"CA", "TX"}, cfg, notes)
    assert notes == []


def test_mismatch_is_not_a_valueerror():
    """The per-source handlers catch ValueError and downgrade to 'source unavailable'.

    A geography mismatch means the run is mis-specified, not under-supplied, so it must
    not be swallowed by those handlers.
    """
    assert not issubclass(GeographyMismatchError, ValueError)
