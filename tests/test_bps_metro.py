"""Metropolitan Building Permits Survey.

The trap here is the mirror image of HMDA's (D036): BPS reports the **parent CBSA** and
never a Metropolitan Division, while HMDA reports divisions and returns zero for the
parent. Keying them to different units would join two different geographies under one
name, which is the failure the whole crosswalk exists to prevent.
"""

from __future__ import annotations

import pytest

from lockin.adapters.census_bps import (
    CBSA_DIRECTORY_FIRST_YEAR,
    METRO_COLUMNS,
    _metro_url,
    _parse_metro,
)

_HEADER = (
    "Survey,CSA,CBSA,MONCOV,CBSA,,1-unit,,,2-units,,,3-4 units,,,5+ units,,,"
    "1-unit rep,,,2-units rep,,,3-4 units rep,,,5+ units rep\n"
    "Date,Code,Code,,Name,Bldgs,Units,Value,Bldgs,Units,Value,Bldgs,Units,Value,"
    "Bldgs,Units,Value,Bldgs,Units,Value,Bldgs,Units,Value,Bldgs,Units,Value,"
    "Bldgs,Units,Value\n \n"
)
_ROW = "202212,176,16980,C,Chicago-Naperville-Elgin  IL-IN-WI ,700,700,180000," + ",".join(
    ["0"] * 21
)


def test_the_directory_splits_at_january_2024():
    """Census published the metro series under two paths with different delineations."""
    assert "Metro%20(ending%202023)" in _metro_url(2023, 12, "c")
    assert "CBSA%20(beginning%20Jan%202024)" in _metro_url(2024, 1, "c")
    assert _metro_url(2023, 12, "c").endswith("ma2312c.txt")
    assert _metro_url(2024, 6, "c").endswith("cbsa2406c.txt")


def test_the_split_year_is_where_it_is_documented():
    assert "Metro" in _metro_url(CBSA_DIRECTORY_FIRST_YEAR - 1, 12, "c")
    assert "CBSA" in _metro_url(CBSA_DIRECTORY_FIRST_YEAR, 1, "c")


def test_header_rows_are_skipped_without_counting_them():
    """Rows are found by a leading YYYYMM, so a changed banner cannot shift the parse."""
    df = _parse_metro(_HEADER + _ROW + "\n")
    assert df.height == 1
    assert df["cbsa_code"][0] == "16980"
    assert df["u1_units"][0] == 700


def test_cbsa_code_stays_text():
    """An identifier, not a quantity: casting to int would drop leading zeros."""
    df = _parse_metro(_HEADER + _ROW + "\n")
    assert isinstance(df["cbsa_code"][0], str)


def test_a_file_with_no_data_rows_raises():
    with pytest.raises(ValueError, match="no data rows"):
        _parse_metro(_HEADER)


def test_column_map_covers_every_published_block():
    """Four structure sizes and four reported-only counterparts, three metrics each."""
    measures = [c for c in METRO_COLUMNS if c.endswith(("_bldgs", "_units", "_value"))]
    assert len(measures) == 8 * 3
    assert "u1_units" in measures and "u5p_rep_units" in measures
