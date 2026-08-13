"""Versioned OMB CBSA crosswalk.

The crosswalk's only job is to answer "does this five-digit code mean the same place in
every vintage?". These tests pin the answer for the cases that matter, especially the
one that a county *count* comparison would miss.
"""

from __future__ import annotations

import polars as pl

from lockin.adapters.omb_cbsa import _long_table, _stability


def _row(vintage, cbsa, title, counties, area_type="Metropolitan Statistical Area", metdiv=None):
    return [
        {
            "vintage": vintage,
            "cbsa_code": cbsa,
            "cbsa_title": title,
            "area_type_raw": area_type,
            "cbsa_type": "metro" if area_type.lower().startswith("metro") else "micro",
            "metdiv_code": metdiv,
            "metdiv_title": f"{title} Division" if metdiv else None,
            "csa_code": None,
            "state_fips": c[:2],
            "county_fips": c[2:],
            "county_geoid": c,
            "central_outlying": "Central",
        }
        for c in counties
    ]


def _frame(rows):
    return pl.DataFrame(rows)


def test_identical_across_vintages_is_stable():
    rows = _row("2013", "10420", "Akron, OH", ["39153", "39133"]) + _row(
        "2023", "10420", "Akron, OH", ["39153", "39133"]
    )
    v = _stability(_long_table(_frame(rows)))
    assert v.filter(pl.col("area_code") == "10420")["verdict"][0] == "stable"


def test_county_swap_at_constant_count_is_composition_changed():
    """The real 2023 Atlanta case: 29 counties before and after, one of them different.

    A naive n_counties comparison calls this stable. It is not -- the code labels a
    different place, and any panel that pools the two is pooling two geographies.
    """
    rows = _row("2013", "12060", "Atlanta, GA", ["13171", "13013"]) + _row(
        "2023", "12060", "Atlanta, GA", ["13187", "13013"]
    )
    v = _stability(_long_table(_frame(rows)))
    row = v.filter(pl.col("area_code") == "12060")
    assert row["verdict"][0] == "composition_changed"
    assert row["min_counties"][0] == row["max_counties"][0] == 2


def test_rename_with_identical_counties_is_only_a_rename():
    """OMB adds and drops principal cities from titles without moving a boundary."""
    rows = _row("2018", "12060", "Atlanta-Sandy Springs-Alpharetta, GA", ["13013"]) + _row(
        "2023", "12060", "Atlanta-Sandy Springs-Roswell, GA", ["13013"]
    )
    v = _stability(_long_table(_frame(rows)))
    row = v.filter(pl.col("area_code") == "12060")
    assert row["verdict"][0] == "renamed_only"
    assert row["composition_stable"][0] is True


def test_absent_vintage_is_flagged_even_when_otherwise_identical():
    rows = (
        _row("2013", "10420", "Akron, OH", ["39153"])
        + _row("2023", "10420", "Akron, OH", ["39153"])
        + _row("2023", "99999", "New Area, XX", ["01001"])
    )
    v = _stability(_long_table(_frame(rows)))
    assert v.filter(pl.col("area_code") == "99999")["verdict"][0] == "absent_in_some_vintage"


def test_metro_to_micro_reclassification_is_flagged():
    rows = _row("2013", "10100", "Aberdeen, SD", ["46013"]) + _row(
        "2023", "10100", "Aberdeen, SD", ["46013"], area_type="Micropolitan Statistical Area"
    )
    v = _stability(_long_table(_frame(rows)))
    assert v.filter(pl.col("area_code") == "10100")["verdict"][0] == "type_changed"


def test_metropolitan_divisions_are_kept_as_separate_codes():
    """Freddie Mac reports an MSA *or* a Metropolitan Division in one field.

    If divisions were folded into their parent CBSA, a division code would silently
    resolve to the whole metro.
    """
    rows = _row("2023", "16980", "Chicago, IL", ["17031"], metdiv="16984")
    long = _long_table(_frame(rows))
    kinds = set(long["code_kind"].to_list())
    assert kinds == {"cbsa", "metdiv"}
    assert set(long.filter(pl.col("code_kind") == "metdiv")["area_code"].to_list()) == {"16984"}


def test_composition_comparison_ignores_row_order():
    """County order differs between published vintages; that is not a redefinition."""
    rows = _row("2013", "10420", "Akron, OH", ["39133", "39153"]) + _row(
        "2023", "10420", "Akron, OH", ["39153", "39133"]
    )
    v = _stability(_long_table(_frame(rows)))
    assert v.filter(pl.col("area_code") == "10420")["verdict"][0] == "stable"
