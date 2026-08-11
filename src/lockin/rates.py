"""Point-in-time market mortgage-rate alignment.

Acceptance criterion 8: *market rates are aligned point in time*. The failure mode
this module exists to prevent is look-ahead bias: attaching to a loan-month the
market rate that was only observable *after* that month, which mechanically
inflates any measured response of prepayment to the rate gap.

Rules implemented here:

1. **PMMS is weekly and dated by survey week.** For a monthly reporting period
   ``m`` we use the last PMMS observation whose survey date is ``<=`` the
   as-of date for ``m``.
2. **The as-of date defaults to the first day of month ``m``.** A borrower acting
   during month ``m`` could only have observed rates published before it began, so
   this is the conservative choice. ``as_of="month_end"`` is available as a
   robustness cell (``month_end_rate_alignment`` in
   ``outputs/hazards/sensitivity_cells``) and is *less* conservative.
3. **Publication lag.** PMMS results are published on the Thursday of the survey
   week. Setting ``publication_lag_days`` (default 0, since the survey date in the
   file already is the Thursday release date) shifts availability further.
4. **Methodology regimes are labeled, not silently spliced.** See
   :data:`METHODOLOGY_REGIMES`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Literal

import polars as pl

AsOf = Literal["month_start", "month_end"]


@dataclass(frozen=True, slots=True)
class MethodologyRegime:
    name: str
    start: date
    end: date | None
    description: str


#: PMMS methodology regimes, from Freddie Mac's published methodology notes.
METHODOLOGY_REGIMES: Final[tuple[MethodologyRegime, ...]] = (
    MethodologyRegime(
        "lender_survey",
        date(1971, 4, 2),
        date(2022, 11, 10),
        "Survey of lenders' offered rates for prime conventional conforming "
        "purchase mortgages with 20% down. Fees/points and 5/1 ARM series were "
        "published throughout this regime.",
    ),
    MethodologyRegime(
        "application_based",
        date(2022, 11, 17),
        None,
        "Methodology changed to be based on loan applications received from "
        "lenders. The fees/points series and the 5/1 ARM series were "
        "DISCONTINUED at this change. Levels before and after are not produced "
        "the same way.",
    ),
)


def methodology_regime(d: date) -> str:
    for r in METHODOLOGY_REGIMES:
        if d >= r.start and (r.end is None or d <= r.end):
            return r.name
    return "unknown"


def month_floor(d: date) -> date:
    return date(d.year, d.month, 1)


def month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    return nxt - timedelta(days=1)


def as_of_date(period: date, mode: AsOf = "month_start") -> date:
    """The date on which the market rate for a monthly period is 'known'."""
    return month_floor(period) if mode == "month_start" else month_end(period)


def monthly_market_rate(
    pmms: pl.DataFrame,
    series: str = "pmms30",
    as_of: AsOf = "month_start",
    publication_lag_days: int = 0,
    first_month: date | None = None,
    last_month: date | None = None,
) -> pl.DataFrame:
    """Collapse weekly PMMS to a point-in-time monthly market rate.

    Parameters
    ----------
    pmms
        Output of :func:`lockin.adapters.pmms.load_pmms`: columns ``date`` (the
        survey/observation date) plus one column per series.
    series
        Which PMMS column to use, e.g. ``pmms30`` or ``pmms15``.
    as_of
        ``month_start`` (default, conservative) or ``month_end``.
    publication_lag_days
        Extra days before an observation is treated as available. The PMMS ``date``
        column is already the Thursday release date, so the default is 0.

    Returns
    -------
    DataFrame with columns ``period`` (first day of month), ``market_rate``,
    ``rate_obs_date``, ``rate_available_from``, ``methodology_regime``,
    ``rate_series``, ``as_of_rule``.

    Notes
    -----
    Months before the first available observation get a null ``market_rate``
    rather than a forward-filled guess.
    """
    if series not in pmms.columns:
        raise KeyError(f"series {series!r} not in PMMS columns {pmms.columns}")

    obs = (
        pmms.select(["date", series])
        .rename({series: "market_rate"})
        .drop_nulls("market_rate")
        .sort("date")
    )
    if obs.height == 0:
        raise ValueError(f"PMMS series {series!r} has no non-null observations")

    obs = obs.with_columns(
        (pl.col("date") + pl.duration(days=publication_lag_days)).alias("rate_available_from"),
        pl.col("date").alias("rate_obs_date"),
    )

    lo = first_month or month_floor(obs["date"].min())  # type: ignore[arg-type]
    hi = last_month or month_floor(obs["date"].max())  # type: ignore[arg-type]

    periods = pl.DataFrame(
        {"period": pl.date_range(month_floor(lo), month_floor(hi), interval="1mo", eager=True)}
    ).with_columns(
        pl.col("period")
        .map_elements(lambda d: as_of_date(d, as_of), return_dtype=pl.Date)
        .alias("_as_of")
    )

    # A backward as-of join takes, for each period, the last observation whose
    # availability date is <= the period's as-of date. This is the no-look-ahead
    # guarantee, enforced by the join semantics rather than by hand.
    joined = periods.sort("_as_of").join_asof(
        obs.sort("rate_available_from"),
        left_on="_as_of",
        right_on="rate_available_from",
        strategy="backward",
    )

    return (
        joined.with_columns(
            pl.col("rate_obs_date")
            .map_elements(
                lambda d: methodology_regime(d) if d is not None else "unknown",
                return_dtype=pl.Utf8,
            )
            .alias("methodology_regime"),
            pl.lit(series).alias("rate_series"),
            pl.lit(as_of).alias("as_of_rule"),
        )
        .select(
            "period",
            "market_rate",
            "rate_obs_date",
            "rate_available_from",
            "methodology_regime",
            "rate_series",
            "as_of_rule",
        )
        .sort("period")
    )


def attach_market_rate(
    df: pl.DataFrame | pl.LazyFrame,
    monthly_rates: pl.DataFrame,
    period_col: str = "period",
) -> pl.LazyFrame:
    """Left-join a point-in-time monthly market rate onto a loan-month table."""
    lf = df.lazy() if isinstance(df, pl.DataFrame) else df
    rates = monthly_rates.select(
        pl.col("period").alias(period_col),
        "market_rate",
        "rate_obs_date",
        "methodology_regime",
        "rate_series",
    ).lazy()
    return lf.join(rates, on=period_col, how="left")


def assert_no_look_ahead(monthly_rates: pl.DataFrame, as_of: AsOf = "month_start") -> None:
    """Hard check that every attached observation predates its as-of date.

    Raises ``AssertionError`` with the offending rows if not. Called by
    ``make validate-data`` and by ``tests/test_rate_alignment.py``.
    """
    chk = monthly_rates.drop_nulls("rate_obs_date").with_columns(
        pl.col("period")
        .map_elements(lambda d: as_of_date(d, as_of), return_dtype=pl.Date)
        .alias("_as_of")
    )
    bad = chk.filter(pl.col("rate_available_from") > pl.col("_as_of"))
    if bad.height:
        raise AssertionError(
            f"look-ahead detected in {bad.height} period(s); first offenders:\n{bad.head(5)}"
        )
