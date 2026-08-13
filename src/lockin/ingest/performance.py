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

from lockin import dataset_stamp
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

from lockin.schemas import variants  # noqa: E402

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


def _modal_field_count(lines: list[str], sample: int = 200) -> int:
    """Most common pipe-field count over the head of a chunk.

    Taken from a sample rather than ``lines[0]`` so that a single ragged or truncated
    line -- which real downloads do contain -- cannot pick the wrong layout variant for
    an entire chunk.
    """
    counts: dict[int, int] = {}
    for line in lines[:sample]:
        n = line.count("|") + 1
        counts[n] = counts.get(n, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else 0


def _pad_ragged(lines: list[str], width: int) -> list[str]:
    """Right-pad rows that are SHORT of the modal width.

    Polars infers the frame width from the first row, so a truncated first line would
    otherwise cap the whole chunk at its width and raise. Padding is only ever additive
    -- the extra cells are empty strings, which the cast step turns into nulls, exactly
    as a genuinely blank field is treated. Over-long rows are left to
    ``truncate_ragged_lines``.
    """
    out = []
    for line in lines:
        short = width - (line.count("|") + 1)
        out.append(line + "|" * short if short > 0 else line)
    return out


def parse_chunk(lines: list[str]) -> pl.DataFrame:
    """Parse a chunk of raw performance lines, projecting to :data:`KEEP`.

    The variant is chosen from the observed field count. Every column in :data:`KEEP`
    sits in positions 1-32, which both known variants share, so the projection is
    identical either way -- but the count is still checked rather than assumed, because
    a layout that shifted the Zero Balance Code by one position would parse cleanly and
    corrupt every event classification downstream.
    """
    n_fields = _modal_field_count(lines) or len(PERFORMANCE_COLUMNS)
    variant = variants.variant_for_performance(n_fields)
    columns = list(variant.performance_columns)
    raw = pl.read_csv(
        io.StringIO("\n".join(_pad_ragged(lines, n_fields))),
        separator="|",
        has_header=False,
        infer_schema_length=0,
        quote_char=None,
        truncate_ragged_lines=True,
        new_columns=columns,
    )
    for c in columns:
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
    # Record which profile owns this directory, so a later run with a different
    # mortgage mode cannot silently read it. See lockin.dataset_stamp.
    dataset_stamp.write(cfg, out_root)
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
    dataset_stamp.check(cfg, root)
    return pl.scan_parquet(root / "**" / "*.parquet")


def _cohorts_on_disk(cfg: Config) -> list[str]:
    root = cfg.path("interim", "performance")
    return sorted(d.name.split("=", 1)[1] for d in root.glob("cohort=*") if d.is_dir())


def _validate_one(lf: pl.LazyFrame) -> dict[str, int | list[str]]:
    """Run every performance check over one partition and return raw counts."""
    dup = (
        lf.group_by(["loan_seq_no", "monthly_reporting_period"])
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") > 1)
        .select(pl.len())
        .collect()
        .item()
    )
    multi_zb = (
        lf.filter(pl.col("zero_balance_code").is_not_null())
        .group_by("loan_seq_no")
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") > 1)
        .select(pl.len())
        .collect()
        .item()
    )
    ordered = lf.sort(["loan_seq_no", "monthly_reporting_period"])
    age_regress = (
        ordered.with_columns(
            (pl.col("loan_age") - pl.col("loan_age").shift(1).over("loan_seq_no")).alias("d_age"),
            pl.col("modification_flag").is_in(["Y", "P"]).alias("modified"),
        )
        .filter((pl.col("d_age") < 0) & ~pl.col("modified"))
        .select(pl.len())
        .collect()
        .item()
    )
    gaps = (
        ordered.with_columns(
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
    neg_upb = lf.filter(pl.col("current_upb") < 0).select(pl.len()).collect().item()
    unknown_zb = (
        lf.filter(
            pl.col("zero_balance_code").is_not_null()
            & ~pl.col("zero_balance_code").is_in(["01", "02", "03", "09", "15", "16", "96"])
        )
        .select(pl.col("zero_balance_code").unique())
        .collect()
    )
    return {
        "dup": dup,
        "multi_zb": multi_zb,
        "age_regress": age_regress,
        "gaps": gaps,
        "neg_upb": neg_upb,
        "unknown_zb": unknown_zb["zero_balance_code"].to_list(),
    }


def validate(cfg: Config) -> list[str]:
    """Performance-level validation checks. Returns human-readable problems.

    Runs **one cohort at a time** and sums the counts. Every check here is per-loan --
    duplicate loan-months, more than one Zero Balance Code, loan-age monotonicity, month
    gaps -- and a loan sequence number encodes its own cohort, so no loan's rows ever
    span two partitions. Per-cohort results are therefore identical to a single pass.

    Why it matters: two of these checks sort the whole table by ``loan_seq_no``. On the
    registered Standard dataset that is 522 million rows, and the single-pass version ran
    for over thirty minutes without finishing while every other validation section
    completed in under two (``DECISION_LOG`` D034). This is the same defect, and the same
    fix, as the loan-event collapse in D032.
    """
    lf = scan(cfg)
    cohorts = _cohorts_on_disk(cfg)
    problems: list[str] = []

    if len(cohorts) <= 1:
        agg = _validate_one(lf)
    else:
        totals = {"dup": 0, "multi_zb": 0, "age_regress": 0, "gaps": 0, "neg_upb": 0}
        codes: set[str] = set()
        for c in cohorts:
            r = _validate_one(lf.filter(pl.col("cohort") == c))
            for k in totals:
                totals[k] += int(r[k])  # type: ignore[arg-type]
            codes.update(r["unknown_zb"])  # type: ignore[arg-type]
        agg = {**totals, "unknown_zb": sorted(codes)}
        problems.append(f"INFO: performance validated across {len(cohorts)} cohort partitions")

    if agg["dup"]:
        problems.append(
            f"HARD: {agg['dup']} duplicate (loan_seq_no, monthly_reporting_period) pairs"
        )
    if agg["multi_zb"]:
        problems.append(f"HARD: {agg['multi_zb']} loans carry more than one Zero Balance Code")
    if agg["age_regress"]:
        problems.append(
            f"SOFT: {agg['age_regress']} loan-months where loan_age decreased without a "
            "modification flag (guide allows resets only on modification)"
        )
    if agg["gaps"]:
        problems.append(
            f"SOFT: {agg['gaps']} within-loan month gaps in the performance series "
            "(missing performance months; handled as unobserved risk time)"
        )
    if agg["neg_upb"]:
        problems.append(f"HARD: {agg['neg_upb']} loan-months with negative current_upb")
    if agg["unknown_zb"]:
        problems.append(
            "HARD: undocumented Zero Balance Code(s) present: "
            f"{agg['unknown_zb']} -- do NOT guess their meaning; "
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
