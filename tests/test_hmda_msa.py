"""Metropolitan-geography HMDA.

Every test here guards against the same failure shape: the CFPB API answers an
unresolvable metro code with ``count: 0`` rather than an error, so a wrong geography
resolution produces a well-formed panel in which the largest markets have no lending.
The traps and the evidence for the year mapping are in ``DECISION_LOG`` D036.
"""

from __future__ import annotations

import polars as pl
import pytest

from lockin.adapters.base import AdapterError
from lockin.adapters.hmda import (
    GEO_PARAM,
    MAX_EMPTY_SHARE,
    _assert_metros_are_not_empty,
    _cache_file,
)
from lockin.adapters.omb_cbsa import (
    HMDA_FIRST_VERIFIED_YEAR,
    vintage_for_hmda_year,
)
from lockin.config import load_config


def _purchase(year: int, counts: list[int]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "year": [year] * len(counts),
            "geography": [f"{10000 + i}" for i in range(len(counts))],
            "measure": ["purchase_originations"] * len(counts),
            "count": counts,
        }
    )


def test_year_to_vintage_matches_what_the_api_actually_returns():
    """Boundaries located by probing, not by an effective-date rule.

    2018 answers on Chicago division 16974 (2015/2017 vintages); 2019 onward answers on
    16984 (2018 vintage); 2024 answers on Atlanta division 12054, which exists only in
    the 2023 vintage.
    """
    assert vintage_for_hmda_year(2018) == "2017"
    assert vintage_for_hmda_year(2019) == "2018"
    assert vintage_for_hmda_year(2023) == "2018"
    assert vintage_for_hmda_year(2024) == "2023"
    assert vintage_for_hmda_year(2030) == "2023"


def test_unverified_years_raise_rather_than_extrapolate():
    with pytest.raises(AdapterError, match="verified"):
        vintage_for_hmda_year(HMDA_FIRST_VERIFIED_YEAR - 1)


def test_a_panel_full_of_empty_metros_raises():
    """The core guard. Zeros here mean a failed lookup, not a market with no lending."""
    df = _purchase(2022, [0] * 8 + [50_000, 40_000])
    with pytest.raises(AdapterError, match="zero purchase originations"):
        _assert_metros_are_not_empty(df)


def test_a_few_genuinely_small_metros_do_not_raise():
    df = _purchase(2022, [0] + [50_000] * 99)
    _assert_metros_are_not_empty(df)


def test_the_guard_is_per_year_not_pooled():
    """One broken year must not be masked by the others.

    A wrong vintage boundary breaks exactly one span of years, so pooling across years
    would dilute a total failure in 2024 below the threshold and let it through.
    """
    good = _purchase(2022, [50_000] * 100)
    broken = _purchase(2024, [0] * 100)
    with pytest.raises(AdapterError, match="2024"):
        _assert_metros_are_not_empty(pl.concat([good, broken]))


def test_threshold_is_where_it_is_documented():
    n = 100
    under = [0] * int(MAX_EMPTY_SHARE * n) + [1] * (n - int(MAX_EMPTY_SHARE * n))
    _assert_metros_are_not_empty(_purchase(2022, under))


def test_state_and_msa_use_different_api_parameters():
    """Both are plural, and both are silently ignored when misspelled (D017)."""
    assert GEO_PARAM["state"] == "states"
    assert GEO_PARAM["msa"] == "msamds"


def test_state_and_msa_cache_keys_cannot_collide():
    cfg = load_config("configs/sample.yaml")
    state = _cache_file(cfg, 2022, "IL", "1", "1", "state")
    msa = _cache_file(cfg, 2022, "16984", "1", "1", "msa")
    assert state != msa
    assert "msa_" in msa.name and "msa_" not in state.name
