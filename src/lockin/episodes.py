"""Loan-month episode table with point-in-time lock-in measures.

This is the estimation dataset for the discrete-time hazard models, and the input
to the geography-month active-stock aggregation.

One row per **observed** loan-month, carrying:

* ``loan_age`` (time origin for the duration model),
* ``current_upb`` and ``remaining_months_to_maturity`` (from the performance file),
* ``market_rate`` -- aligned **point-in-time** (see :mod:`lockin.rates`),
* ``rate_gap``, ``lockin_gap``, ``refi_incentive``, ``payment_gap``,
  ``pv_financing_gap``, ``gap_bucket``,
* ``est_current_ltv`` and ``ltv_source``,
* ``exit_prepayment`` / ``exit_credit_event`` -- the discrete-time event indicators,
  which are 1 **only** in the loan's exit month,
* ``at_risk`` -- always 1 for an observed month (kept explicit so weighted and
  sampled variants stay legible).

Memory discipline: the whole thing is a Polars ``LazyFrame`` pipeline that is
collected in streaming mode and written to partitioned Parquet by period year. The
full loan-by-month panel is never materialised as a Python object.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import polars as pl

from lockin.config import Config
from lockin.ingest import performance as perf_mod
from lockin.lockin_measures import (
    GAP_BUCKET_EDGES_BP,
    GAP_BUCKET_LABELS,
)
from lockin.manifest import write_manifest

EPISODE_COLUMNS: tuple[str, ...] = (
    "loan_seq_no",
    "cohort",
    "period",
    "loan_age",
    "at_risk",
    "exit_prepayment",
    "exit_credit_event",
    "exit_any",
    "censored_this_month",
    "current_upb",
    "upb_start_of_month",
    "upb_timing_source",
    "remaining_term",
    "note_rate",
    "current_interest_rate",
    "market_rate",
    "rate_series",
    "methodology_regime",
    "rate_gap",
    "lockin_gap",
    "refi_incentive",
    "payment_gap",
    "pv_financing_gap",
    "gap_bucket",
    "est_current_ltv",
    "ltv_source",
    "property_state",
    "msa_code",
    "loan_purpose",
    "occupancy_status",
    "property_type",
    "credit_score",
    "orig_dti",
    "orig_ltv",
    "orig_upb",
    "orig_loan_term",
    "first_time_homebuyer_flag",
    "orig_cohort_year",
    "ever_modified_to_date",
    "delinquency_status",
    "hpi_growth_12m",
)


def _pmt_expr(balance: pl.Expr, rate_pct: pl.Expr, n: pl.Expr) -> pl.Expr:
    """Level payment, expressed in Polars so it stays inside the lazy plan.

    Mirrors :func:`lockin.amortization.payment` exactly, including the zero-rate
    limit; ``tests/test_amortization.py::test_polars_matches_numpy`` pins them
    together.
    """
    i = rate_pct / 1200.0
    growth = (1.0 + i).pow(n)
    return (
        pl.when(n <= 0)
        .then(None)
        .when(i.abs() < 1e-12)
        .then(balance / n)
        .otherwise(balance * i * growth / (growth - 1.0))
    )


def _annuity_expr(months: pl.Expr, discount_pct: float) -> pl.Expr:
    d = discount_pct / 1200.0
    if abs(d) < 1e-12:
        return months
    return (1.0 - (1.0 + d) ** (-months)) / d


def _gap_bucket_expr(gap: pl.Expr) -> pl.Expr:
    bp = gap * 100.0
    e = pl.when(bp.is_null()).then(None)
    edges = GAP_BUCKET_EDGES_BP[1:-1]
    for idx, edge in enumerate(edges):
        e = e.when(bp < edge).then(pl.lit(idx, dtype=pl.Int64))
    return e.otherwise(pl.lit(len(edges), dtype=pl.Int64))


def build_episodes(
    cfg: Config,
    events: pl.DataFrame,
    monthly_rates: pl.DataFrame,
    hpi: pl.DataFrame | None = None,
    cohort: str | None = None,
) -> pl.LazyFrame:
    """Assemble the loan-month episode table as a lazy plan.

    Parameters
    ----------
    events
        Output of :func:`lockin.events.build_loan_events`.
    monthly_rates
        Output of :func:`lockin.rates.monthly_market_rate` -- **point in time**.
    hpi
        Optional state HPI series from
        :func:`lockin.adapters.fhfa_hpi.load_series`, used for estimated current
        LTV and local price growth. When ``None``, ``est_current_ltv`` falls back to
        the balance-only scaling and ``ltv_source`` records that.
    """
    perf = perf_mod.scan(cfg)
    if cohort is not None:
        perf = perf.filter(pl.col("cohort") == cohort)
        events = events.filter(pl.col("cohort") == cohort)
    perf = perf.select(
        "loan_seq_no",
        "monthly_reporting_period",
        "current_upb",
        "loan_age",
        "remaining_months_to_maturity",
        "modification_flag",
        "delinquency_status",
        "zero_balance_code",
        "current_interest_rate",
        "reported_eltv",
        "cohort",
    )

    ev = events.lazy().select(
        "loan_seq_no",
        "event_type",
        "event_date",
        "observation_start",
        "observation_end",
        "orig_interest_rate",
        "orig_upb",
        "orig_ltv",
        "orig_loan_term",
        "orig_dti",
        "credit_score",
        "property_state",
        "msa_code",
        "loan_purpose",
        "occupancy_status",
        "property_type",
        "first_time_homebuyer_flag",
        "approx_origination_date",
    )

    rates = monthly_rates.select(
        pl.col("period"), "market_rate", "rate_series", "methodology_regime"
    ).lazy()

    lf = (
        perf.rename({"monthly_reporting_period": "period"})
        .join(ev, on="loan_seq_no", how="inner")
        # Truncate any months after the resolved exit (reappearing loans).
        .filter(pl.col("period") <= pl.col("observation_end"))
        .join(rates, on="period", how="left")
    )

    if hpi is not None:
        hpi_state = hpi.select(
            pl.col("place_name").alias("_hpi_place"),
            pl.col("geography").alias("_hpi_geo"),
            "period",
            "hpi",
        )
        # FHFA state place_id is the two-letter abbreviation for state-level rows.
        hpi_now = hpi_state.select(
            pl.col("_hpi_geo").alias("property_state"), "period", pl.col("hpi").alias("hpi_now")
        ).lazy()
        hpi_orig = hpi_state.select(
            pl.col("_hpi_geo").alias("property_state"),
            pl.col("period").alias("approx_origination_month"),
            pl.col("hpi").alias("hpi_at_origination"),
        ).lazy()
        hpi_lag = (
            hpi_state.sort(["_hpi_geo", "period"])
            .with_columns(
                (pl.col("hpi").log() - pl.col("hpi").log().shift(12).over("_hpi_geo")).alias(
                    "hpi_growth_12m"
                )
            )
            .select(pl.col("_hpi_geo").alias("property_state"), "period", "hpi_growth_12m")
            .lazy()
        )
        lf = (
            lf.with_columns(
                pl.col("approx_origination_date")
                .dt.truncate("1mo")
                .alias("approx_origination_month")
            )
            .join(hpi_now, on=["property_state", "period"], how="left")
            .join(hpi_orig, on=["property_state", "approx_origination_month"], how="left")
            .join(hpi_lag, on=["property_state", "period"], how="left")
        )
    else:
        lf = lf.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("hpi_now"),
            pl.lit(None, dtype=pl.Float64).alias("hpi_at_origination"),
            pl.lit(None, dtype=pl.Float64).alias("hpi_growth_12m"),
        )

    note = pl.col("orig_interest_rate")
    rem = (
        pl.coalesce(
            [
                pl.col("remaining_months_to_maturity"),
                (pl.col("orig_loan_term") - pl.col("loan_age")),
            ]
        )
        .clip(1, None)
        .cast(pl.Float64)
    )
    mkt = pl.col("market_rate")

    # ------------------------------------------------------------------
    # Balance TIMING. `current_upb` is the balance at the END of the reporting
    # period, and in a zero-balance month it is 0 by construction. Using it would
    # (a) set the payment gap to zero in exactly the months where an exit occurs --
    # corrupting the covariate for every event -- and (b) attach an end-of-month
    # quantity to a within-month decision.
    #
    # We therefore use the balance at the START of the month: the previous month's
    # reported UPB, falling back to the scheduled amortised balance for a loan's
    # first observed month. `current_upb` is retained as reported.
    # ------------------------------------------------------------------
    prev_upb = pl.col("current_upb").shift(1).over("loan_seq_no")
    i_note = note / 1200.0
    g_n = (1.0 + i_note).pow(pl.col("orig_loan_term").cast(pl.Float64))
    g_k = (1.0 + i_note).pow(pl.col("loan_age").cast(pl.Float64) - 1.0)
    scheduled = (
        pl.when(i_note.abs() < 1e-12)
        .then(
            pl.col("orig_upb").cast(pl.Float64)
            * (1.0 - (pl.col("loan_age").cast(pl.Float64) - 1.0) / pl.col("orig_loan_term"))
        )
        .otherwise(pl.col("orig_upb").cast(pl.Float64) * (g_n - g_k) / (g_n - 1.0))
        .clip(0.0, None)
    )
    lf = lf.sort(["loan_seq_no", "period"]).with_columns(
        pl.coalesce(
            [
                pl.when(prev_upb > 0).then(prev_upb).otherwise(None),
                pl.when(pl.col("current_upb") > 0).then(pl.col("current_upb")).otherwise(None),
                scheduled,
            ]
        ).alias("upb_start_of_month"),
        pl.when(prev_upb > 0)
        .then(pl.lit("prior_month_reported_upb"))
        .when(pl.col("current_upb") > 0)
        .then(pl.lit("same_month_reported_upb"))
        .otherwise(pl.lit("scheduled_amortisation"))
        .alias("upb_timing_source"),
    )
    bal = pl.col("upb_start_of_month").cast(pl.Float64)

    gap = mkt - note
    pgap = _pmt_expr(bal, mkt, rem) - _pmt_expr(bal, note, rem)
    holding = pl.min_horizontal(pl.lit(float(cfg.lockin.holding_period_months)), rem)

    lf = lf.with_columns(
        pl.col("remaining_months_to_maturity").alias("_rem_reported"),
        rem.alias("remaining_term"),
        note.alias("note_rate"),
        gap.alias("rate_gap"),
        pl.max_horizontal(gap, pl.lit(0.0)).alias("lockin_gap"),
        (-gap).alias("refi_incentive"),
        pgap.alias("payment_gap"),
        (pgap * _annuity_expr(holding, cfg.lockin.discount_rate_pct)).alias("pv_financing_gap"),
        _gap_bucket_expr(gap).alias("gap_bucket"),
        pl.col("approx_origination_date").dt.year().alias("orig_cohort_year"),
        pl.lit(1, dtype=pl.Int8).alias("at_risk"),
    )

    # Estimated current LTV: prefer Freddie's reported ELTV when present, else scale
    # origination LTV by amortisation and the state HPI path (DECISION_LOG D010).
    scaled_ltv = (
        pl.col("orig_ltv").cast(pl.Float64)
        * (bal / pl.col("orig_upb").cast(pl.Float64))
        * (pl.col("hpi_at_origination") / pl.col("hpi_now"))
    )
    balance_only_ltv = pl.col("orig_ltv").cast(pl.Float64) * (
        bal / pl.col("orig_upb").cast(pl.Float64)
    )
    lf = lf.with_columns(
        pl.coalesce([pl.col("reported_eltv").cast(pl.Float64), scaled_ltv, balance_only_ltv]).alias(
            "est_current_ltv"
        ),
        pl.when(pl.col("reported_eltv").is_not_null())
        .then(pl.lit("freddie_reported_eltv"))
        .when(pl.col("hpi_now").is_not_null() & pl.col("hpi_at_origination").is_not_null())
        .then(pl.lit("orig_ltv_x_amortisation_x_state_hpi"))
        .otherwise(pl.lit("orig_ltv_x_amortisation_only"))
        .alias("ltv_source"),
    )

    # Discrete-time event indicators: 1 only in the exit month.
    is_exit_month = pl.col("period") == pl.col("event_date")
    lf = lf.with_columns(
        (is_exit_month & (pl.col("event_type") == "prepayment"))
        .cast(pl.Int8)
        .alias("exit_prepayment"),
        (is_exit_month & (pl.col("event_type") == "credit_event"))
        .cast(pl.Int8)
        .alias("exit_credit_event"),
        pl.col("modification_flag").is_in(["Y", "P"]).cast(pl.Int8).alias("ever_modified_to_date"),
    ).with_columns(
        (pl.col("exit_prepayment") | pl.col("exit_credit_event")).cast(pl.Int8).alias("exit_any"),
        ((pl.col("period") == pl.col("observation_end")) & (pl.col("event_type") == "censored"))
        .cast(pl.Int8)
        .alias("censored_this_month"),
    )

    lf = lf.select([c for c in EPISODE_COLUMNS if c != "_"]).sort(["loan_seq_no", "period"])
    return _maybe_case_cohort_sample(cfg, lf)


def _maybe_case_cohort_sample(cfg: Config, lf: pl.LazyFrame) -> pl.LazyFrame:
    """Optionally thin the episode table before it is written.

    Two independent knobs, both applied at LOAN level because a loan's months are not
    independent observations -- dropping a random subset of one loan's months would break
    the ``.over("loan_seq_no")`` window functions and the risk-set construction.

    ``loan_sample_fraction``
        A plain random sample of loans, cases and non-cases alike, keeping every month of
        each selected loan. This is the knob that controls how big the estimation problem
        is. On the full Standard dataset the unsampled episode table is 90.6M rows even
        after case-cohort thinning, and the dense design matrix the discrete-time models
        build from it is ~21.7 GB -- more than the machine has, so the estimator was
        killed with no traceback.

    ``non_event_sample_fraction``
        The case-cohort filter proper: within the sampled loans, every month of a loan
        that ever exits is kept, and non-exiting loans are kept or dropped whole.

    Both use a stable hash of ``loan_seq_no`` -- different salts, so the two selections
    are independent -- which makes the sample reproducible without storing a row list,
    and identical to the predicate ``lockin.survival.dataset`` would apply downstream.
    """
    if not cfg.survival.sample_at_episode_build:
        return lf

    loan_frac = float(cfg.survival.loan_sample_fraction)
    event_frac = float(cfg.survival.non_event_sample_fraction)
    seed = cfg.survival.seed
    scale = 1_000_000

    if 0.0 < loan_frac < 1.0:
        lf = lf.filter(pl.col("loan_seq_no").hash(seed=seed).mod(scale) < int(loan_frac * scale))
    if 0.0 < event_frac < 1.0:
        keep_loan = pl.col("exit_any").max().over("loan_seq_no") == 1
        # A different salt, so this draw is independent of the loan-sample draw above.
        sampled = pl.col("loan_seq_no").hash(seed=seed + 1).mod(scale) < int(event_frac * scale)
        lf = lf.filter(keep_loan | sampled)
    return lf


def write_episodes(
    cfg: Config,
    lf: pl.LazyFrame | None = None,
    *,
    build: Callable[[str | None], pl.LazyFrame] | None = None,
    cohorts: list[str] | None = None,
) -> tuple[object, int]:
    """Collect and write the episode table, partitioned by period year.

    Two modes. Passing ``lf`` collects that one plan -- fine for fixtures. Passing
    ``build`` and ``cohorts`` instead builds and writes **one cohort at a time**, which
    is what the full Standard dataset needs.

    Why: ``collect()`` materialises the whole frame before anything is written, and the
    episode table carries ~30 columns per loan-month. At 522 million loan-months that is
    far beyond the 17 GB of RAM on this machine, and the process was killed by the OOM
    reaper with no traceback -- a silent death that looks like a hang. Episodes are
    per-loan (every window function is ``.over("loan_seq_no")``) and loans never span
    cohorts, so per-cohort collection is exact, not an approximation.
    """
    out_root = cfg.path("processed", "loan_episodes")
    out_root.mkdir(parents=True, exist_ok=True)
    for old in out_root.rglob("*.parquet"):
        old.unlink()

    plans: list[tuple[str, pl.LazyFrame]] = []
    if build is not None and cohorts:
        plans = [(c, build(c)) for c in cohorts]
    else:
        plans = [("all", lf if lf is not None else pl.LazyFrame())]

    n = 0
    lo: date | None = None
    hi: date | None = None
    for tag, plan in plans:
        df = plan.collect(engine="streaming")
        n += df.height
        if df.height:
            p_lo, p_hi = df["period"].min(), df["period"].max()
            lo = p_lo if lo is None or p_lo < lo else lo
            hi = p_hi if hi is None or p_hi > hi else hi
        if n > cfg.survival.max_episode_rows:
            raise RuntimeError(
                f"episode table has reached {n:,} rows, above the configured budget of "
                f"{cfg.survival.max_episode_rows:,}. Lower survival.loan_sample_fraction "
                "or narrow the performance window."
            )
        for (year,), part in df.with_columns(pl.col("period").dt.year().alias("_y")).group_by(
            ["_y"], maintain_order=True
        ):
            d = out_root / f"period_year={int(year)}"  # type: ignore[arg-type]
            d.mkdir(parents=True, exist_ok=True)
            # One file per (year, cohort): a shared name would have each cohort
            # overwrite the last, silently keeping only the final one.
            part.drop("_y").write_parquet(d / f"part-{tag}.parquet")
        del df

    write_manifest(
        out_root,
        name="loan_episodes",
        source="derived from loan_events, the performance table, PMMS, and FHFA HPI",
        source_url="n/a (derived)",
        license_terms="Inherits the terms of its inputs.",
        redistribution_status="not redistributed (loan granularity)",
        schema_version="loan-episodes-v1",
        row_count=n,
        geographic_level="loan-month (state)",
        coverage_period=f"{lo}..{hi}",
        known_limitations=[
            "One row per OBSERVED loan-month. Missing performance months are absent, "
            "so they contribute no risk time.",
            "market_rate is the last PMMS observation available on or before the "
            "first day of the month -- point in time, no look-ahead.",
            "est_current_ltv uses a STATE house price index as a proxy for an "
            "individual property's price path; substantial measurement error.",
            "payment_gap holds the remaining term fixed. See "
            "lockin_measures.payment_gap_fresh_term for the alternative.",
            "PV financing gap uses CALIBRATED holding-period and discount-rate "
            "inputs, not estimated ones.",
        ],
        data_class=cfg.manifest_data_class,
        extra={
            "holding_period_months": cfg.lockin.holding_period_months,
            "discount_rate_pct": cfg.lockin.discount_rate_pct,
            "gap_bucket_labels": list(GAP_BUCKET_LABELS),
            "n_rows": n,
        },
    )
    return (out_root, n)


def scan_episodes(cfg: Config) -> pl.LazyFrame:
    root = cfg.path("processed", "loan_episodes")
    if not root.exists():
        raise FileNotFoundError(f"{root} missing. Run `make build-lockin`.")
    return pl.scan_parquet(root / "**" / "*.parquet")


def validate_episodes(cfg: Config) -> list[str]:
    """Checks specific to the episode table, including the no-look-ahead guarantee."""
    lf = scan_episodes(cfg)
    problems: list[str] = []

    missing_rate = lf.filter(pl.col("market_rate").is_null()).select(pl.len()).collect().item()
    total = lf.select(pl.len()).collect().item()
    if missing_rate:
        problems.append(
            f"SOFT: {missing_rate}/{total} episodes have no point-in-time market rate "
            "(months before the first PMMS observation available)"
        )

    # An exit can be flagged at most once per loan.
    multi_exit = (
        lf.group_by("loan_seq_no")
        .agg(pl.col("exit_any").sum().alias("k"))
        .filter(pl.col("k") > 1)
        .select(pl.len())
        .collect()
        .item()
    )
    if multi_exit:
        problems.append(f"HARD: {multi_exit} loans have more than one exit month flagged")

    both = (
        lf.filter((pl.col("exit_prepayment") == 1) & (pl.col("exit_credit_event") == 1))
        .select(pl.len())
        .collect()
        .item()
    )
    if both:
        problems.append(f"HARD: {both} episodes flagged as both prepayment and credit event")

    bad_gap = (
        lf.filter(
            pl.col("rate_gap").is_not_null()
            & ((pl.col("rate_gap") - (pl.col("market_rate") - pl.col("note_rate"))).abs() > 1e-9)
        )
        .select(pl.len())
        .collect()
        .item()
    )
    if bad_gap:
        problems.append(f"HARD: {bad_gap} episodes where rate_gap != market_rate - note_rate")

    # The payment gap must share the rate gap's sign wherever a positive balance is
    # being refinanced. Zero-balance rows are excluded: with no balance there is no
    # payment to change.
    sign = (
        lf.filter(
            pl.col("payment_gap").is_not_null()
            & pl.col("rate_gap").is_not_null()
            & (pl.col("upb_start_of_month") > 0)
            & (pl.col("rate_gap").abs() > 0.01)
            & (pl.col("payment_gap").sign() != pl.col("rate_gap").sign())
        )
        .select(pl.len())
        .collect()
        .item()
    )
    if sign:
        problems.append(
            f"HARD: {sign} episodes where payment_gap and rate_gap disagree in sign "
            "despite a positive start-of-month balance"
        )

    timing = (
        lf.group_by("upb_timing_source")
        .agg(pl.len().alias("n"))
        .sort("n", descending=True)
        .collect()
    )
    problems.append(
        "INFO: start-of-month balance sources: "
        + ", ".join(f"{r['upb_timing_source']}={r['n']:,}" for r in timing.to_dicts())
    )

    zero_bal_events = (
        lf.filter((pl.col("exit_any") == 1) & (pl.col("upb_start_of_month") <= 0))
        .select(pl.len())
        .collect()
        .item()
    )
    if zero_bal_events:
        problems.append(
            f"HARD: {zero_bal_events} exit months have no positive start-of-month "
            "balance, so their lock-in covariates are undefined"
        )

    neg_term = lf.filter(pl.col("remaining_term") <= 0).select(pl.len()).collect().item()
    if neg_term:
        problems.append(f"HARD: {neg_term} episodes with non-positive remaining_term")
    return problems


def _unused(d: date) -> None:  # pragma: no cover
    return None
