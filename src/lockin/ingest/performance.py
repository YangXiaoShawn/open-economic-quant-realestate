"""Monthly-performance-file parser.

Pipe-delimited, no header, 32 fields. This is the file that is enormous in the
full dataset (billions of loan-months), so the parser:

* streams line chunks from :func:`lockin.adapters.freddie_llds.iter_lines`,
* projects away the ~18 loss/expense columns we never use, keeping the table
  compact,
* filters to the configured performance window during parsing so out-of-window
  months are never written,
* writes partitioned Parquet at
  ``data/interim/performance/cohort=YYYYQn/period_year=YYYY/part-*.parquet``,
  which lets Polars/DuckDB prune both cohort and year at scan time.

Peak memory is O(chunk_rows), not O(file).
"""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import date

import polars as pl

from lockin.adapters import freddie_llds as llds
from lockin.config import Config
from lockin.manifest import write_manifest
from lockin.schemas.freddie import (
    PERFORMANCE_COLUMNS,
    PERFORMANCE_FIELDS,
    SCHEMA_VERSION,
)

#: Columns retained. The loss/expense block (fields 14-23, 27, 28, 31) is dropped:
#: it is only needed for loss-severity work, which is out of scope, and it roughly
#: halves the on-disk footprint.
KEEP: tuple[str, ...] = (
    "loan_seq_no",
    "monthly_reporting_period",
    "current_upb",
    "delinquency_status",
    "loan_age",
    "remaining_months_to_maturity",
    "defect_settlement_date",
    "modification_flag",
    "zero_balance_code",
    "zero_balance_effective_date",
    "current_interest_rate",
    "current_deferred_upb",
    "ddlpi",
    "step_modification_flag",
    "deferred_payment_plan",
    "reported_eltv",
    "delinquency_due_to_disaster",
    "borrower_assistance_status",
    "interest_bearing_upb",
)

_FIELD_BY_NAME = {f.name: f for f in PERFORMANCE_FIELDS}


def _cast_expressions() -> list[pl.Expr]:
    exprs: list[pl.Expr] = []
    for name in KEEP:
        f = _FIELD_BY_NAME[name]
        col = pl.col(name).str.strip_chars()
        if f.na_values:
            col = col.replace(dict.fromkeys(f.na_values))
        col = col.replace({"": None})
        if f.dtype == "yyyymm":
            col = col.str.to_date(format="%Y%m", strict=False)
        elif f.dtype == "int":
            col = col.cast(pl.Int64, strict=False)
        elif f.dtype in ("float", "rate", "money"):
            col = col.cast(pl.Float64, strict=False)
        exprs.append(col.alias(name))
    return exprs


def parse_chunk(lines: list[str]) -> pl.DataFrame:
    """Parse a chunk of raw performance lines, projecting to :data:`KEEP`."""
    raw = pl.read_csv(
        io.StringIO("\n".join(lines)),
        separator="|",
        has_header=False,
        infer_schema_length=0,
        quote_char=None,
        truncate_ragged_lines=True,
        new_columns=list(PERFORMANCE_COLUMNS),
    )
    for c in PERFORMANCE_COLUMNS:
        if c not in raw.columns:
            raw = raw.with_columns(pl.lit(None, dtype=pl.Utf8).alias(c))
    return raw.select(list(KEEP)).with_columns(_cast_expressions())


def _window(cfg: Config) -> tuple[date, date]:
    y0, m0 = map(int, cfg.mortgage.performance_start.split("-"))
    y1, m1 = map(int, cfg.mortgage.performance_end.split("-"))
    return (date(y0, m0, 1), date(y1, m1, 1))


def ingest(cfg: Config, cohorts: list[str] | None = None) -> dict[str, int]:
    """Parse performance files into partitioned Parquet. Returns ``{cohort: rows}``."""
    want = cohorts or cfg.mortgage.cohorts
    files = llds.files_for(cfg, "performance", want)
    source_label = "registered"
    if not files:
        files = [
            f
            for f in llds.discover(cfg, root=cfg.path("fixtures", "freddie"))
            if f.kind == "performance" and f.cohort in set(want)
        ]
        source_label = "synthetic"
    if not files:
        raise RuntimeError("no performance files found. Run `make prepare-sample-data` first.")

    lo, hi = _window(cfg)
    out_root = cfg.path("interim", "performance")
    counts: dict[str, int] = {}

    for lf in files:
        cohort_dir = out_root / f"cohort={lf.cohort}"
        if cohort_dir.exists():
            for old in cohort_dir.rglob("part-*.parquet"):
                old.unlink()
        part_counters: dict[int, int] = defaultdict(int)
        n = 0
        for lines in llds.iter_lines(lf, cfg.mortgage.chunk_rows):
            df = parse_chunk(lines).filter(pl.col("monthly_reporting_period").is_between(lo, hi))
            if df.height == 0:
                continue
            df = df.with_columns(
                pl.col("monthly_reporting_period").dt.year().alias("period_year"),
                pl.lit(lf.cohort).alias("cohort"),
            )
            for (year,), part in df.group_by(["period_year"], maintain_order=True):
                ydir = cohort_dir / f"period_year={int(year)}"  # type: ignore[arg-type]
                ydir.mkdir(parents=True, exist_ok=True)
                idx = part_counters[int(year)]  # type: ignore[arg-type]
                part.drop("period_year").write_parquet(ydir / f"part-{idx:05d}.parquet")
                part_counters[int(year)] = idx + 1  # type: ignore[arg-type]
                n += part.height
        counts[lf.cohort] = n

    total = sum(counts.values())
    write_manifest(
        out_root,
        name="performance_table",
        source=llds.SOURCE
        if source_label == "registered"
        else "SYNTHETIC fixtures (lockin.fixtures)",
        source_url=llds.SOURCE_URL if source_label == "registered" else "n/a (synthetic)",
        license_terms=llds.LICENSE_TERMS
        if source_label == "registered"
        else "Synthetic; freely redistributable.",
        redistribution_status=llds.REDISTRIBUTION_STATUS
        if source_label == "registered"
        else "synthetic -- freely redistributable",
        schema_version=SCHEMA_VERSION,
        row_count=total,
        geographic_level="loan-month",
        coverage_period=f"{lo.isoformat()}..{hi.isoformat()}",
        known_limitations=list(llds.KNOWN_LIMITATIONS)
        + [
            "The loss/expense columns (performance fields 14-23, 27, 28, 31) are "
            "PROJECTED AWAY at parse time; loss-severity analysis is out of scope.",
            f"Rows are filtered to {lo}..{hi} at parse time.",
        ],
        data_class="RESTRICTED" if source_label == "registered" else "SYNTHETIC",
        extra={
            "rows_by_cohort": counts,
            "kept_columns": list(KEEP),
            "source_files": [f.describe() for f in files],
        },
    )
    return counts


def scan(cfg: Config) -> pl.LazyFrame:
    """Lazy scan over the partitioned performance table (hive-partitioned)."""
    root = cfg.path("interim", "performance")
    if not root.exists():
        raise FileNotFoundError(f"{root} missing. Run `make ingest-mortgages`.")
    return pl.scan_parquet(root / "**" / "*.parquet")


def validate(cfg: Config) -> list[str]:
    """Performance-level validation checks. Returns human-readable problems."""
    lf = scan(cfg)
    problems: list[str] = []

    dup = (
        lf.group_by(["loan_seq_no", "monthly_reporting_period"])
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") > 1)
        .select(pl.len())
        .collect()
        .item()
    )
    if dup:
        problems.append(f"HARD: {dup} duplicate (loan_seq_no, monthly_reporting_period) pairs")

    # A loan may carry a Zero Balance Code at most once (user guide, "Zero Balance
    # Codes"). More than one is a hard error.
    multi_zb = (
        lf.filter(pl.col("zero_balance_code").is_not_null())
        .group_by("loan_seq_no")
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") > 1)
        .select(pl.len())
        .collect()
        .item()
    )
    if multi_zb:
        problems.append(f"HARD: {multi_zb} loans carry more than one Zero Balance Code")

    # Loan age should be non-decreasing within a loan, EXCEPT across a
    # modification, where the guide says loan age resets.
    age_regress = (
        lf.sort(["loan_seq_no", "monthly_reporting_period"])
        .with_columns(
            (pl.col("loan_age") - pl.col("loan_age").shift(1).over("loan_seq_no")).alias("d_age"),
            pl.col("modification_flag").is_in(["Y", "P"]).alias("modified"),
        )
        .filter((pl.col("d_age") < 0) & ~pl.col("modified"))
        .select(pl.len())
        .collect()
        .item()
    )
    if age_regress:
        problems.append(
            f"SOFT: {age_regress} loan-months where loan_age decreased without a "
            "modification flag (guide allows resets only on modification)"
        )

    gaps = (
        lf.sort(["loan_seq_no", "monthly_reporting_period"])
        .with_columns(
            (
                (
                    pl.col("monthly_reporting_period").dt.year() * 12
                    + pl.col("monthly_reporting_period").dt.month()
                )
                - (
                    pl.col("monthly_reporting_period").shift(1).over("loan_seq_no").dt.year() * 12
                    + pl.col("monthly_reporting_period").shift(1).over("loan_seq_no").dt.month()
                )
            ).alias("gap")
        )
        .filter(pl.col("gap") > 1)
        .select(pl.len())
        .collect()
        .item()
    )
    if gaps:
        problems.append(
            f"SOFT: {gaps} within-loan month gaps in the performance series "
            "(missing performance months; handled as unobserved risk time)"
        )

    neg_upb = lf.filter(pl.col("current_upb") < 0).select(pl.len()).collect().item()
    if neg_upb:
        problems.append(f"HARD: {neg_upb} loan-months with negative current_upb")

    unknown_zb = (
        lf.filter(
            pl.col("zero_balance_code").is_not_null()
            & ~pl.col("zero_balance_code").is_in(["01", "02", "03", "09", "15", "16", "96"])
        )
        .select(pl.col("zero_balance_code").unique())
        .collect()
    )
    if unknown_zb.height:
        problems.append(
            "HARD: undocumented Zero Balance Code(s) present: "
            f"{unknown_zb['zero_balance_code'].to_list()} -- do NOT guess their meaning; "
            "check the current user guide and update lockin.schemas.freddie"
        )
    return problems


def summary(cfg: Config) -> dict[str, object]:
    lf = scan(cfg)
    tot = (
        lf.select(
            pl.len().alias("n_loan_months"),
            pl.col("loan_seq_no").n_unique().alias("n_loans"),
            pl.col("monthly_reporting_period").min().alias("period_min"),
            pl.col("monthly_reporting_period").max().alias("period_max"),
        )
        .collect()
        .to_dicts()[0]
    )
    for k, v in list(tot.items()):
        if isinstance(v, date):
            tot[k] = v.isoformat()
    zb = (
        lf.filter(pl.col("zero_balance_code").is_not_null())
        .group_by("zero_balance_code")
        .agg(pl.len().alias("n"))
        .sort("zero_balance_code")
        .collect()
    )
    return {"totals": tot, "zero_balance_counts": zb.to_dicts()}
