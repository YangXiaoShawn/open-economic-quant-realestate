"""Point-in-time market-rate alignment: the no-look-ahead guarantee.

Acceptance criterion 8. Look-ahead bias here would mechanically inflate any
measured response of prepayment to the rate gap, so this is tested against both a
synthetic series with a known answer and (when cached) the real PMMS file.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from lockin.rates import (
    METHODOLOGY_REGIMES,
    as_of_date,
    assert_no_look_ahead,
    methodology_regime,
    month_end,
    month_floor,
    monthly_market_rate,
)


def _weekly(start: date, weeks: int, values: list[float] | None = None) -> pl.DataFrame:
    dates = pl.date_range(
        start, start + __import__("datetime").timedelta(weeks=weeks - 1), interval="1w", eager=True
    )
    vals = values if values is not None else [3.0 + 0.1 * i for i in range(len(dates))]
    return pl.DataFrame({"date": dates, "pmms30": vals[: len(dates)]})


def test_month_helpers() -> None:
    assert month_floor(date(2022, 7, 19)) == date(2022, 7, 1)
    assert month_end(date(2022, 2, 3)) == date(2022, 2, 28)
    assert month_end(date(2024, 2, 3)) == date(2024, 2, 29)  # leap year
    assert month_end(date(2022, 12, 15)) == date(2022, 12, 31)
    assert as_of_date(date(2022, 7, 15), "month_start") == date(2022, 7, 1)
    assert as_of_date(date(2022, 7, 15), "month_end") == date(2022, 7, 31)


def test_no_look_ahead_on_synthetic_series() -> None:
    pmms = _weekly(date(2021, 1, 7), 120)
    monthly = monthly_market_rate(pmms, "pmms30", as_of="month_start")
    assert_no_look_ahead(monthly, "month_start")
    joined = monthly.drop_nulls("rate_obs_date")
    assert (joined["rate_obs_date"] <= joined["period"]).all()


def test_month_start_picks_the_last_observation_before_the_month() -> None:
    """The rate for month m must be the last survey on or before the 1st of m."""
    pmms = pl.DataFrame(
        {
            "date": [
                date(2022, 1, 6),
                date(2022, 1, 13),
                date(2022, 1, 20),
                date(2022, 1, 27),
                date(2022, 2, 3),
                date(2022, 2, 10),
            ],
            "pmms30": [3.22, 3.45, 3.56, 3.55, 3.55, 3.69],
        }
    )
    monthly = monthly_market_rate(pmms, "pmms30", as_of="month_start", last_month=date(2022, 3, 1))
    feb = monthly.filter(pl.col("period") == date(2022, 2, 1))
    assert feb.height == 1
    # 2022-02-01 precedes the 2022-02-03 survey, so January's last survey applies.
    assert feb["market_rate"][0] == pytest.approx(3.55)
    assert feb["rate_obs_date"][0] == date(2022, 1, 27)
    # March can see February's last survey.
    mar = monthly.filter(pl.col("period") == date(2022, 3, 1))
    assert mar["market_rate"][0] == pytest.approx(3.69)
    # January is NULL: its as-of date (2022-01-01) precedes the first survey
    # (2022-01-06). A forward fill here would be look-ahead bias.
    jan = monthly.filter(pl.col("period") == date(2022, 1, 1))
    assert jan["market_rate"][0] is None


def test_month_end_is_less_conservative_and_labeled() -> None:
    pmms = pl.DataFrame(
        {
            "date": [date(2022, 1, 6), date(2022, 1, 27), date(2022, 2, 3)],
            "pmms30": [3.22, 3.55, 3.99],
        }
    )
    start = monthly_market_rate(pmms, "pmms30", as_of="month_start")
    end = monthly_market_rate(pmms, "pmms30", as_of="month_end")
    jan_s = start.filter(pl.col("period") == date(2022, 1, 1))["market_rate"][0]
    jan_e = end.filter(pl.col("period") == date(2022, 1, 1))["market_rate"][0]
    feb_s = start.filter(pl.col("period") == date(2022, 2, 1))["market_rate"][0]
    feb_e = end.filter(pl.col("period") == date(2022, 2, 1))["market_rate"][0]
    # month_end can see later surveys within the same month; month_start cannot.
    assert jan_e == pytest.approx(3.55)
    assert jan_s is None  # 2022-01-01 precedes the first survey
    assert feb_s == pytest.approx(3.55)  # January's last survey
    assert feb_e == pytest.approx(3.99)  # February's own survey
    assert start["as_of_rule"][0] == "month_start"
    assert end["as_of_rule"][0] == "month_end"
    assert_no_look_ahead(end, "month_end")


def test_publication_lag_pushes_availability_forward() -> None:
    pmms = pl.DataFrame({"date": [date(2022, 1, 27), date(2022, 2, 24)], "pmms30": [3.55, 3.89]})
    lagged = monthly_market_rate(pmms, "pmms30", as_of="month_start", publication_lag_days=10)
    feb = lagged.filter(pl.col("period") == date(2022, 2, 1))
    # With a 10-day lag the 2022-01-27 survey is only available from 2022-02-06,
    # which is after 2022-02-01, so February has no rate.
    assert feb["market_rate"][0] is None
    assert_no_look_ahead(lagged, "month_start")


def test_months_before_the_first_observation_are_null_not_filled() -> None:
    pmms = _weekly(date(2022, 6, 3), 10)
    monthly = monthly_market_rate(
        pmms, "pmms30", first_month=date(2022, 1, 1), last_month=date(2022, 8, 1)
    )
    early = monthly.filter(pl.col("period") < date(2022, 6, 1))
    assert early.height > 0
    assert early["market_rate"].null_count() == early.height


def test_assert_no_look_ahead_detects_a_violation() -> None:
    """The guard must actually fire; a test that only checks the happy path is
    not a test of the guard."""
    bad = pl.DataFrame(
        {
            "period": [date(2022, 1, 1)],
            "market_rate": [5.0],
            "rate_obs_date": [date(2022, 3, 15)],
            "rate_available_from": [date(2022, 3, 15)],
            "methodology_regime": ["lender_survey"],
            "rate_series": ["pmms30"],
            "as_of_rule": ["month_start"],
        }
    )
    with pytest.raises(AssertionError, match="look-ahead"):
        assert_no_look_ahead(bad, "month_start")


def test_methodology_regimes_cover_the_change_date() -> None:
    assert methodology_regime(date(2021, 6, 3)) == "lender_survey"
    assert methodology_regime(date(2022, 11, 10)) == "lender_survey"
    assert methodology_regime(date(2022, 11, 17)) == "application_based"
    assert methodology_regime(date(2025, 1, 2)) == "application_based"
    assert methodology_regime(date(1960, 1, 1)) == "unknown"
    assert len(METHODOLOGY_REGIMES) == 2


def test_unknown_series_raises() -> None:
    with pytest.raises(KeyError, match="not in PMMS columns"):
        monthly_market_rate(_weekly(date(2022, 1, 7), 10), "pmms_does_not_exist")


def test_all_null_series_raises() -> None:
    df = pl.DataFrame({"date": [date(2022, 1, 7)], "pmms30": [None]}).with_columns(
        pl.col("pmms30").cast(pl.Float64)
    )
    with pytest.raises(ValueError, match="no non-null observations"):
        monthly_market_rate(df, "pmms30")


@pytest.mark.network
def test_real_pmms_has_no_look_ahead() -> None:
    """Against the actual cached PMMS file, if it has been fetched."""
    from lockin.adapters import pmms
    from lockin.config import load_config

    cfg = load_config("configs/sample.yaml")
    try:
        raw = pmms.load(cfg)
    except FileNotFoundError:
        pytest.skip("PMMS not cached; run `make fetch-public-data`")
    monthly = monthly_market_rate(raw, cfg.rates.series)
    assert_no_look_ahead(monthly)
    assert monthly.height > 500
    # The 2022 increase must be present in the real data.
    dec21 = monthly.filter(pl.col("period") == date(2021, 12, 1))["market_rate"][0]
    oct23 = monthly.filter(pl.col("period") == date(2023, 10, 1))["market_rate"][0]
    assert oct23 - dec21 > 3.0
