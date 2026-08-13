"""Loan-event construction.

Produces two tables:

``loan_events`` -- one row per loan::

    loan_seq_no, cohort, entry_date, observation_start, observation_end,
    start_age, end_age, event_type, event_date, censored, censoring_reason,
    zero_balance_code, ever_modified, n_months_observed, n_month_gaps,
    <origination characteristics>

``loan_episodes`` -- one row per observed loan-month, with time-varying UPB,
estimated current LTV, point-in-time market rate, and the lock-in measures.

The event taxonomy is fixed by the official Zero Balance Code priority table and
is **not** negotiable at the call site:

===============  =========================================================
``prepayment``   ZB 01 -- "Prepaid or Matured (Voluntary Payoff)". Conflates
                 voluntary payoff with maturity and does NOT distinguish
                 refinance from sale-related payoff.
``credit_event`` ZB 02 / 03 / 09.
``censored``     No ZB by the performance cutoff, OR ZB 15 / 16 / 96
                 (Freddie Mac portfolio and R&W actions -- not borrower
                 behaviour), OR an undocumented ZB code.
===============  =========================================================

There is **no** ``home_sale`` and no ``household_move`` event type, and there is no
code path that can create one. See ``AGENTS.md`` §1.

Handled explicitly:

* **Left truncation** -- ``observation_start`` is the first observed performance
  month, which begins at Freddie Mac *acquisition*, not origination. ``start_age``
  is the loan age at that month and is >1 for acquisition-lagged loans.
* **Right censoring** -- at the performance cutoff or at an administrative removal.
* **Missing performance months** -- counted in ``n_month_gaps``; the episode table
  contains only observed months, so gap months contribute no risk time.
* **Modifications** -- flagged; loan age resets are tolerated by the validator and
  ``modification_reset`` marks affected loans.
* **Conflicting event codes** -- if a loan somehow carries two ZB codes, the
  official priority table decides (lower ``priority`` number wins).
* **Reappearing loans** -- a loan with performance months *after* its ZB month is
  flagged ``reappeared_after_exit`` and truncated at the exit.
* **Geography changes** -- origination geography is fixed by construction (the
  origination file has one state per loan); a change would indicate a join error
  and is checked.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from lockin.config import Config
from lockin.ingest import origination as orig_mod
from lockin.ingest import performance as perf_mod
from lockin.manifest import write_manifest
from lockin.schemas.freddie import ZERO_BALANCE_CODES

EVENT_TYPES: tuple[str, ...] = ("prepayment", "credit_event", "censored")

_ZB_TO_EVENT: dict[str, str] = {
    code: ("censored" if zb.censoring else zb.event_class)
    for code, zb in ZERO_BALANCE_CODES.items()
}
_ZB_PRIORITY: dict[str, int] = {code: zb.priority for code, zb in ZERO_BALANCE_CODES.items()}
_ZB_LABEL: dict[str, str] = {code: zb.official_label for code, zb in ZERO_BALANCE_CODES.items()}

ORIGINATION_KEEP: tuple[str, ...] = (
    "loan_seq_no",
    "cohort",
    "credit_score",
    "first_payment_date",
    "first_time_homebuyer_flag",
    "maturity_date",
    "msa_code",
    "num_units",
    "occupancy_status",
    "orig_cltv",
    "orig_dti",
    "orig_upb",
    "orig_ltv",
    "orig_interest_rate",
    "channel",
    "amortization_type",
    "property_state",
    "property_type",
    "loan_purpose",
    "orig_loan_term",
    "seller_name",
    "servicer_name",
    "super_conforming_flag",
    "relief_refi_indicator",
    "interest_only_indicator",
    "approx_origination_date",
)


def _event_expr(col: str = "zero_balance_code") -> pl.Expr:
    """Map a ZB code column to an event type, with an explicit unknown-code branch."""
    e = pl.when(pl.col(col).is_null()).then(pl.lit("censored"))
    for code, ev in _ZB_TO_EVENT.items():
        e = e.when(pl.col(col) == code).then(pl.lit(ev))
    # Undocumented codes are censored, never guessed at.
    return e.otherwise(pl.lit("censored")).alias("event_type")


def _censoring_reason_expr() -> pl.Expr:
    e = pl.when(pl.col("zero_balance_code").is_null()).then(pl.lit("active_at_performance_cutoff"))
    for code, zb in ZERO_BALANCE_CODES.items():
        if zb.censoring:
            e = e.when(pl.col("zero_balance_code") == code).then(
                pl.lit(f"admin_removal_zb{code}_{zb.official_label.lower().replace(' ', '_')}")
            )
        else:
            e = e.when(pl.col("zero_balance_code") == code).then(pl.lit(None, dtype=pl.Utf8))
    return e.otherwise(pl.lit("undocumented_zb_code")).alias("censoring_reason")


def build_loan_events(cfg: Config, cohorts: list[str] | None = None) -> pl.DataFrame:
    """Collapse the performance panel to one event row per loan and join origination.

    Processed **one cohort at a time** and concatenated. The collapse is a global sort on
    ``loan_seq_no`` followed by a per-loan aggregation, and on the full Standard dataset
    that sort is over half a billion rows -- which spilled roughly 20 GB of Polars
    scratch and came within 6 GB of filling the disk on a 522-million-row run before it
    was stopped.

    Partitioning is exact rather than approximate: a loan sequence number encodes its own
    origination cohort (``F21Q4...``) and the interim tables are already hive-partitioned
    by cohort, so no loan's rows ever span two partitions. Per-cohort results are
    therefore identical to the single-pass result, and peak scratch is one cohort instead
    of forty.
    """
    available = sorted(_available_cohorts(cfg))
    wanted = [c for c in (cohorts or cfg.mortgage.cohorts or available) if c in available]
    if not wanted:
        wanted = available
    if len(wanted) <= 1:
        return _build_one(cfg, wanted[0] if wanted else None)

    frames = [_build_one(cfg, c) for c in wanted]
    frames = [f for f in frames if f.height]
    if not frames:
        return _build_one(cfg, None)
    return pl.concat(frames, how="vertical_relaxed").sort("loan_seq_no")


def _available_cohorts(cfg: Config) -> set[str]:
    root = cfg.path("interim", "performance")
    if not root.exists():
        return set()
    out: set[str] = set()
    for p in root.glob("cohort=*"):
        if p.is_dir():
            out.add(p.name.split("=", 1)[1])
    return out


def _build_one(cfg: Config, cohort: str | None) -> pl.DataFrame:
    """The original single-pass collapse, scoped to one cohort when given."""
    perf = perf_mod.scan(cfg)
    orig = orig_mod.scan(cfg).select(list(ORIGINATION_KEEP))
    if cohort is not None:
        perf = perf.filter(pl.col("cohort") == cohort)
        orig = orig.filter(pl.col("cohort") == cohort)

    # Resolve the exit month per loan: the row carrying a ZB code, choosing by the
    # official priority table if (contrary to the guide) more than one exists.
    zb_rows = (
        perf.filter(pl.col("zero_balance_code").is_not_null())
        .with_columns(
            pl.col("zero_balance_code")
            .replace_strict(_ZB_PRIORITY, default=99, return_dtype=pl.Int64)
            .alias("_zb_priority")
        )
        .sort(["loan_seq_no", "_zb_priority", "monthly_reporting_period"])
        .group_by("loan_seq_no", maintain_order=True)
        .agg(
            pl.col("zero_balance_code").first(),
            pl.col("zero_balance_effective_date").first().alias("zb_effective_date"),
            pl.col("monthly_reporting_period").first().alias("zb_reported_period"),
            pl.len().alias("n_zb_rows"),
        )
    )

    spans = perf.group_by("loan_seq_no").agg(
        pl.col("monthly_reporting_period").min().alias("observation_start"),
        pl.col("monthly_reporting_period").max().alias("last_observed_period"),
        pl.col("loan_age").min().alias("start_age"),
        pl.col("loan_age").max().alias("end_age"),
        pl.len().alias("n_months_observed"),
        pl.col("modification_flag").is_in(["Y", "P"]).any().alias("ever_modified"),
        (pl.col("loan_age").diff() < 0).any().alias("modification_reset"),
        pl.col("current_upb").last().alias("last_upb"),
        pl.col("delinquency_status").is_in(["1", "2", "3"]).any().alias("ever_30d_plus"),
    )

    joined = spans.join(zb_rows, on="loan_seq_no", how="left").join(
        orig, on="loan_seq_no", how="inner"
    )

    events = (
        joined.with_columns(
            _event_expr(),
            _censoring_reason_expr(),
            pl.coalesce(["zb_effective_date", "zb_reported_period"]).alias("event_date"),
        )
        .with_columns(
            pl.col("event_type").eq("censored").alias("censored"),
            pl.col("first_payment_date").alias("entry_date"),
            # Observation end: the exit month if there is one, else the last month
            # observed (right censoring at the performance cutoff).
            pl.when(pl.col("event_date").is_not_null())
            .then(pl.min_horizontal("event_date", "last_observed_period"))
            .otherwise(pl.col("last_observed_period"))
            .alias("observation_end"),
            pl.col("zero_balance_code")
            .replace_strict(_ZB_LABEL, default=None, return_dtype=pl.Utf8)
            .alias("zero_balance_label"),
            (pl.col("n_zb_rows") > 1).fill_null(False).alias("conflicting_zb_codes"),
        )
        .with_columns(
            # Expected number of months between start and end if no gaps.
            (
                (pl.col("observation_end").dt.year() * 12 + pl.col("observation_end").dt.month())
                - (
                    pl.col("observation_start").dt.year() * 12
                    + pl.col("observation_start").dt.month()
                )
                + 1
            ).alias("_span_months")
        )
        .with_columns(
            (pl.col("_span_months") - pl.col("n_months_observed"))
            .clip(0, None)
            .alias("n_month_gaps"),
            (pl.col("last_observed_period") > pl.col("event_date"))
            .fill_null(False)
            .alias("reappeared_after_exit"),
            pl.lit(False).alias("home_sale_observed"),
        )
        .drop("_span_months")
        .collect()
    )

    return events.sort("loan_seq_no")


def validate_events(events: pl.DataFrame) -> list[str]:
    """Invariants that must hold for the survival design to be coherent."""
    problems: list[str] = []

    if events.height == 0:
        return ["HARD: loan-event table is empty"]

    n_dup = events.height - events["loan_seq_no"].n_unique()
    if n_dup:
        problems.append(f"HARD: {n_dup} duplicate loans in the event table")

    bad_order = events.filter(pl.col("observation_end") < pl.col("observation_start")).height
    if bad_order:
        problems.append(f"HARD: {bad_order} loans with observation_end < observation_start")

    early_exit = events.filter(
        pl.col("event_date").is_not_null() & (pl.col("event_date") < pl.col("observation_start"))
    ).height
    if early_exit:
        problems.append(f"HARD: {early_exit} loans with an exit before observation_start")

    bad_event = events.filter(~pl.col("event_type").is_in(list(EVENT_TYPES))).height
    if bad_event:
        problems.append(f"HARD: {bad_event} loans with an event_type outside {EVENT_TYPES}")

    # Exits + censored must partition the loan population exactly.
    counts = events.group_by("event_type").agg(pl.len().alias("n")).to_dicts()
    total = sum(int(c["n"]) for c in counts)
    if total != events.height:
        problems.append(f"HARD: event types sum to {total} but there are {events.height} loans")

    # ZB 15/16/96 must be censored, never an exit.
    leak = events.filter(
        pl.col("zero_balance_code").is_in(["15", "16", "96"]) & ~pl.col("censored")
    ).height
    if leak:
        problems.append(
            f"HARD: {leak} loans with an administrative-removal ZB code (15/16/96) "
            "were not censored -- this would treat a Freddie Mac portfolio action as "
            "borrower behaviour"
        )

    # Nothing may ever claim to observe a home sale.
    if events["home_sale_observed"].any():
        problems.append(
            "HARD: home_sale_observed is True somewhere. No field in this dataset "
            "supports a home-sale event. See AGENTS.md section 1."
        )

    conflicting = int(events["conflicting_zb_codes"].sum())
    if conflicting:
        problems.append(
            f"SOFT: {conflicting} loans carried more than one Zero Balance Code; "
            "resolved by the official priority table"
        )
    reappeared = int(events["reappeared_after_exit"].sum())
    if reappeared:
        problems.append(
            f"SOFT: {reappeared} loans have performance months after their exit month; "
            "truncated at the exit"
        )
    trunc = events.filter(pl.col("start_age") > 1).height
    if trunc:
        problems.append(
            f"INFO: {trunc} of {events.height} loans are LEFT TRUNCATED "
            f"({100 * trunc / events.height:.1f}%): first observed at loan age > 1. "
            "Two distinct causes are combined here -- (a) the Freddie Mac ACQUISITION "
            "lag, since performance records begin at acquisition rather than "
            "origination, and (b) the configured PERFORMANCE WINDOW, which truncates "
            "any cohort originated before it. Both are handled identically by the "
            "risk sets, but only (a) is a property of the data."
        )
        window_trunc = events.filter(
            pl.col("observation_start") == events["observation_start"].min()
        ).height
        problems.append(
            f"INFO: of those, {window_trunc} enter in the FIRST month of the "
            "performance window, i.e. their truncation is a window artifact rather "
            "than an acquisition lag"
        )
    gaps = events.filter(pl.col("n_month_gaps") > 0).height
    if gaps:
        problems.append(
            f"INFO: {gaps} loans have at least one missing performance month; those "
            "months contribute no risk time"
        )
    return problems


def event_summary(events: pl.DataFrame) -> dict[str, Any]:
    """Descriptive summary for the validation report and the hazard report.

    Tolerates a frame without ``zero_balance_label`` by deriving it from the official
    code map, so the function works on any frame carrying the required event columns.
    """
    if "zero_balance_label" not in events.columns:
        events = events.with_columns(
            # Cast first: an all-null column arrives with dtype Null, and
            # replace_strict cannot map str keys onto it.
            pl.col("zero_balance_code")
            .cast(pl.Utf8)
            .replace_strict(_ZB_LABEL, default=None, return_dtype=pl.Utf8)
            .alias("zero_balance_label")
        )
    by_type = (
        events.group_by("event_type")
        .agg(pl.len().alias("n_loans"), pl.col("orig_upb").sum().alias("orig_upb"))
        .sort("n_loans", descending=True)
    )
    by_zb = (
        events.filter(pl.col("zero_balance_code").is_not_null())
        .group_by(["zero_balance_code", "zero_balance_label", "event_type", "censored"])
        .agg(pl.len().alias("n_loans"))
        .sort("zero_balance_code")
    )
    return {
        "n_loans": events.height,
        "by_event_type": by_type.to_dicts(),
        "by_zero_balance_code": by_zb.to_dicts(),
        "n_left_truncated": events.filter(pl.col("start_age") > 1).height,
        "median_start_age": float(events["start_age"].median() or 0),
        "n_ever_modified": int(events["ever_modified"].sum()),
        "n_with_month_gaps": events.filter(pl.col("n_month_gaps") > 0).height,
        "n_conflicting_zb": int(events["conflicting_zb_codes"].sum()),
        "n_reappeared_after_exit": int(events["reappeared_after_exit"].sum()),
        "observation_start_min": str(events["observation_start"].min()),
        "observation_end_max": str(events["observation_end"].max()),
        "outcome_definitions": {
            "prepayment": "Zero Balance Code 01 'Prepaid or Matured (Voluntary Payoff)'. "
            "CONFLATES voluntary payoff and scheduled maturity; does NOT "
            "distinguish refinance from sale-related payoff. NOT a home "
            "sale and NOT a household move.",
            "credit_event": "Zero Balance Codes 02 (Third Party Sale), 03 (Short Sale "
            "or Charge Off), 09 (REO Disposition).",
            "censored": "No Zero Balance Code by the performance cutoff, or ZB 15/16/96 "
            "(whole-loan sale, RPL securitization, defect prior to other "
            "termination) -- Freddie Mac portfolio actions, not borrower "
            "behaviour.",
        },
    }


def write_loan_events(cfg: Config, events: pl.DataFrame) -> tuple[Path, dict[str, Any]]:
    """Persist the loan-event table with a manifest. Returns ``(path, summary)``."""
    out = cfg.path("processed", "loan_events.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    events.write_parquet(out)
    summ = event_summary(events)
    write_manifest(
        out,
        name="loan_events",
        source="derived from the origination and monthly performance tables",
        source_url="n/a (derived)",
        license_terms="Derived from restricted or synthetic inputs; inherits their terms.",
        redistribution_status="not redistributed (loan granularity)",
        schema_version="loan-events-v1",
        row_count=events.height,
        geographic_level="loan (state)",
        coverage_period=f"{events['observation_start'].min()}..{events['observation_end'].max()}",
        known_limitations=[
            "prepayment = ZB 01 only. Conflates voluntary payoff with maturity; does "
            "not distinguish refinance from sale-related payoff. NOT a move.",
            "ZB 15/16/96 are censored, which assumes their removal is uninformative "
            "about the borrower's latent exit time. Bounded by the "
            "admin_removals_as_prepayment cell in outputs/hazards/sensitivity_cells.",
            "Left truncation at Freddie Mac acquisition is respected but the "
            "acquisition process itself may be selective.",
            "Missing performance months contribute no risk time.",
        ],
        data_class=cfg.manifest_data_class,
        extra={"summary": summ},
    )
    return (out, summ)


def load_loan_events(cfg: Config) -> pl.DataFrame:
    p = cfg.path("processed", "loan_events.parquet")
    if not p.exists():
        raise FileNotFoundError(f"{p} missing. Run `make build-loan-events`.")
    return pl.read_parquet(p)


def maturity_like_prepayments(events: pl.DataFrame, threshold_months: int = 3) -> int:
    """Count prepayments occurring within ``threshold_months`` of scheduled maturity.

    A **heuristic filter**, not an event classification: ZB 01 covers both voluntary
    payoff and scheduled maturity, and no field separates them. Reported so readers
    can see how much of the prepayment count could be maturity rather than an
    active decision. In a modern cohort this is essentially zero.
    """
    if "maturity_date" not in events.columns:
        return 0
    return events.filter(
        (pl.col("event_type") == "prepayment")
        & pl.col("event_date").is_not_null()
        & (
            (
                (pl.col("maturity_date").dt.year() * 12 + pl.col("maturity_date").dt.month())
                - (pl.col("event_date").dt.year() * 12 + pl.col("event_date").dt.month())
            )
            <= threshold_months
        )
    ).height


def _unused(d: date) -> None:  # pragma: no cover
    return None
