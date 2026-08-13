"""Origination-file parser.

Pipe-delimited, no header, 32 fields (verified layout -- see
``lockin.schemas.freddie``). Streams by chunk and writes partitioned Parquet at
``data/interim/origination/cohort=YYYYQn/part-*.parquet``.

Normalisations applied, all documented in ``data/DATA_DICTIONARY.md``:

* ``YYYYMM`` date fields -> ``pl.Date`` on the first of the month.
* Official sentinel codes (``9999`` credit score, ``999`` DTI/LTV/CLTV/MI, ``99``
  units/borrowers, ``9`` flags, blank MSA/postal) -> ``null``.
* Rates and money -> ``Float64``.
* Blank strings -> ``null``.
* ``orig_year_quarter`` derived from the loan sequence number prefix and
  cross-checked against the file's cohort.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import polars as pl

from lockin import dataset_stamp
from lockin.adapters import freddie_llds as llds
from lockin.config import Config
from lockin.manifest import write_manifest
from lockin.schemas import variants
from lockin.schemas.freddie import (
    ORIGINATION_COLUMNS,
    ORIGINATION_FIELDS,
    SCHEMA_VERSION,
)


def _cast_expressions(fields=ORIGINATION_FIELDS) -> list[pl.Expr]:
    exprs: list[pl.Expr] = []
    for f in fields:
        col = pl.col(f.name).str.strip_chars()
        if f.na_values:
            col = col.replace(dict.fromkeys(f.na_values))
        col = col.replace({"": None})
        if f.dtype == "yyyymm":
            col = col.str.to_date(format="%Y%m", strict=False)
        elif f.dtype == "int":
            col = col.cast(pl.Int64, strict=False)
        elif f.dtype in ("float", "rate", "money"):
            col = col.cast(pl.Float64, strict=False)
        exprs.append(col.alias(f.name))
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
    """Parse a chunk of raw origination lines into a typed DataFrame.

    The layout variant is chosen from the observed field count, not assumed. Freddie Mac
    ships 31 origination fields in the 2026 full set and 32 in the documented layout; the
    two agree on fields 1-24 and diverge after, so applying the wrong names would leave
    every research variable correct while silently mislabelling the tail -- a value of
    "N" landing in ``servicer_name``, for instance. See ``lockin.schemas.variants``.
    """
    n_fields = _modal_field_count(lines) or len(ORIGINATION_COLUMNS)
    variant = variants.variant_for_origination(n_fields)
    columns = list(variant.origination_columns)
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
    out = raw.select(columns).with_columns(_cast_expressions(variant.origination))
    # Fields that moved to the performance file in this variant are absent here. Add
    # them as nulls so the downstream schema is stable across variants.
    for c in ORIGINATION_COLUMNS:
        if c not in out.columns:
            out = out.with_columns(pl.lit(None, dtype=pl.Utf8).alias(c))
    return out.with_columns(pl.lit(variant.key).alias("layout_variant"))


def _derive(df: pl.DataFrame, cohort: str) -> pl.DataFrame:
    """Add derived columns and a documented cohort cross-check."""
    return df.with_columns(
        pl.lit(cohort).alias("cohort"),
        # The loan sequence number encodes product and origination year/quarter:
        # PYYQn.  We derive it and keep it so a mismatch with the file cohort is
        # visible rather than silent.
        pl.col("loan_seq_no").str.slice(0, 1).alias("seq_product"),
        ("20" + pl.col("loan_seq_no").str.slice(1, 2))
        .cast(pl.Int64, strict=False)
        .alias("seq_orig_year"),
        pl.col("loan_seq_no")
        .str.slice(4, 1)
        .cast(pl.Int64, strict=False)
        .alias("seq_orig_quarter"),
        # Origination month is not a field; first payment date less ~2 months is
        # the conventional approximation and is labeled as an approximation.
        pl.col("first_payment_date").dt.offset_by("-2mo").alias("approx_origination_date"),
        pl.col("property_state").alias("geography_state"),
    ).with_columns(
        pl.when(pl.col("orig_loan_term").is_not_null() & pl.col("first_payment_date").is_not_null())
        .then(pl.col("first_payment_date").dt.offset_by("-2mo").dt.year())
        .otherwise(None)
        .alias("approx_origination_year")
    )


def ingest(cfg: Config, cohorts: list[str] | None = None) -> dict[str, int]:
    """Parse every available origination file into partitioned Parquet.

    Falls back to the synthetic fixture directory when no registered files exist.
    Returns ``{cohort: row_count}``.
    """
    want = cohorts or cfg.mortgage.cohorts
    files = llds.files_for(cfg, "origination", want)
    source_label = "registered"
    if not files:
        files = _fixture_files(cfg, "origination", want)
        source_label = "synthetic"
    if not files:
        raise RuntimeError(
            "no origination files found in either data/raw/freddie or data/fixtures/freddie. "
            "Run `make prepare-sample-data` first."
        )

    out_root = cfg.path("interim", "origination")
    # Record which profile owns this directory, so a later run with a different
    # mortgage mode cannot silently read it. See lockin.dataset_stamp.
    dataset_stamp.write(cfg, out_root)
    counts: dict[str, int] = {}
    for lf in files:
        part_dir = out_root / f"cohort={lf.cohort}"
        part_dir.mkdir(parents=True, exist_ok=True)
        for old in part_dir.glob("part-*.parquet"):
            old.unlink()
        n = 0
        for idx, lines in enumerate(llds.iter_lines(lf, cfg.mortgage.chunk_rows)):
            df = _derive(parse_chunk(lines), lf.cohort)
            df.write_parquet(part_dir / f"part-{idx:05d}.parquet")
            n += df.height
        counts[lf.cohort] = n

    total = sum(counts.values())
    write_manifest(
        out_root,
        name="origination_table",
        source=f"{llds.SOURCE} ({source_label})"
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
        geographic_level="loan (state, MSA where populated)",
        coverage_period=",".join(sorted(counts)),
        known_limitations=list(llds.KNOWN_LIMITATIONS),
        data_class="RESTRICTED" if source_label == "registered" else "SYNTHETIC",
        extra={"rows_by_cohort": counts, "source_files": [f.describe() for f in files]},
    )
    return counts


def _fixture_files(cfg: Config, kind: str, cohorts: list[str]) -> list[llds.LoanFile]:
    return [
        f
        for f in llds.discover(cfg, root=cfg.path("fixtures", "freddie"))
        if f.kind == kind and f.cohort in set(cohorts)
    ]


def scan(cfg: Config) -> pl.LazyFrame:
    """Lazy scan over the partitioned origination table."""
    root = cfg.path("interim", "origination")
    if not root.exists():
        raise FileNotFoundError(f"{root} missing. Run `make ingest-mortgages`.")
    dataset_stamp.check(cfg, root)
    return pl.scan_parquet(root / "**" / "*.parquet")


#: Above this share of records, an out-of-domain value is a systematic problem -- a
#: layout shift or a parsing error -- and the run must stop. Below it, it is source-data
#: noise: real registered files do contain a handful of impossible values, and a gate
#: that always fires on real data teaches people to ignore it.
DOMAIN_VIOLATION_HARD_SHARE: float = 1e-4


def _domain_problem(label: str, n_bad: int, n_total: int, detail: str) -> str:
    """Severity scaled by prevalence, with the observed values always reported."""
    share = n_bad / max(n_total, 1)
    level = "HARD" if share > DOMAIN_VIOLATION_HARD_SHARE else "SOFT"
    return (
        f"{level}: {n_bad:,} of {n_total:,} loans ({share:.3%}) have {label}. "
        f"Observed: {detail}. "
        + (
            "That share is too high to be source noise -- suspect a layout shift or a "
            "parsing error before accepting it."
            if level == "HARD"
            else "Retained and reported; too rare to indicate a parsing fault, but real "
            "and worth knowing when a magnitude is quoted."
        )
    )


def validate(cfg: Config) -> list[str]:
    """Origination-level validation. Returns a list of human-readable problems."""
    lf = scan(cfg)
    problems: list[str] = []

    n, n_unique = (
        lf.select(pl.len().alias("n"), pl.col("loan_seq_no").n_unique().alias("u")).collect().row(0)
    )
    if n != n_unique:
        problems.append(f"HARD: {n - n_unique} duplicate loan_seq_no in origination ({n} rows)")
    n_loans = int(n)

    bad_term = (
        lf.filter((pl.col("orig_loan_term") <= 0) | (pl.col("orig_loan_term") > 480))
        .select(pl.len())
        .collect()
        .item()
    )
    if bad_term:
        vals = (
            lf.filter((pl.col("orig_loan_term") <= 0) | (pl.col("orig_loan_term") > 480))
            .select(pl.col("orig_loan_term").unique().sort().head(6))
            .collect()["orig_loan_term"]
            .to_list()
        )
        problems.append(
            _domain_problem("orig_loan_term outside (0, 480]", bad_term, n_loans, str(vals))
        )

    bad_rate = (
        lf.filter((pl.col("orig_interest_rate") <= 0) | (pl.col("orig_interest_rate") > 20))
        .select(pl.len())
        .collect()
        .item()
    )
    if bad_rate:
        vals = (
            lf.filter((pl.col("orig_interest_rate") <= 0) | (pl.col("orig_interest_rate") > 20))
            .select(pl.col("orig_interest_rate").unique().sort().head(6))
            .collect()["orig_interest_rate"]
            .to_list()
        )
        problems.append(
            _domain_problem("orig_interest_rate outside (0, 20]", bad_rate, n_loans, str(vals))
        )

    bad_upb = lf.filter(pl.col("orig_upb") <= 0).select(pl.len()).collect().item()
    if bad_upb:
        problems.append(f"HARD: {bad_upb} loans with non-positive orig_upb")

    # The maturity date should equal first payment + term - 1 by the official
    # definition of Original Loan Term. A mismatch is a soft warning because
    # modified loans and data-quality quirks exist in real files.
    mism = (
        lf.with_columns(
            (
                (pl.col("maturity_date").dt.year() * 12 + pl.col("maturity_date").dt.month())
                - (
                    pl.col("first_payment_date").dt.year() * 12
                    + pl.col("first_payment_date").dt.month()
                )
                + 1
            ).alias("implied_term")
        )
        .filter(pl.col("implied_term") != pl.col("orig_loan_term"))
        .select(pl.len())
        .collect()
        .item()
    )
    if mism:
        problems.append(f"SOFT: {mism} loans where maturity - first_payment + 1 != orig_loan_term")

    seq_mismatch = (
        lf.filter(
            pl.col("seq_orig_year").is_not_null()
            & (pl.col("seq_orig_year") != pl.col("cohort").str.slice(0, 4).cast(pl.Int64))
        )
        .select(pl.len())
        .collect()
        .item()
    )
    if seq_mismatch:
        problems.append(
            f"SOFT: {seq_mismatch} loans whose sequence-number year differs from the "
            "file cohort (expected for seasoned or repurchased loans)"
        )
    return problems


def summary(cfg: Config) -> dict[str, object]:
    """Small descriptive summary of the origination table for validation reports."""
    lf = scan(cfg)
    agg = lf.select(
        pl.len().alias("n_loans"),
        pl.col("orig_upb").sum().alias("total_orig_upb"),
        pl.col("orig_interest_rate").mean().alias("mean_note_rate"),
        pl.col("orig_interest_rate").median().alias("median_note_rate"),
        pl.col("credit_score").mean().alias("mean_credit_score"),
        pl.col("orig_ltv").mean().alias("mean_orig_ltv"),
        pl.col("property_state").n_unique().alias("n_states"),
        pl.col("first_payment_date").min().alias("first_payment_min"),
        pl.col("first_payment_date").max().alias("first_payment_max"),
    ).collect()
    by_purpose = lf.group_by("loan_purpose").agg(pl.len().alias("n")).sort("loan_purpose").collect()
    by_state = (
        lf.group_by("property_state")
        .agg(pl.len().alias("n"), pl.col("orig_interest_rate").mean().alias("mean_note_rate"))
        .sort("n", descending=True)
        .collect()
    )
    d = agg.to_dicts()[0]
    for k, v in list(d.items()):
        if isinstance(v, date):
            d[k] = v.isoformat()
    return {
        "totals": d,
        "by_loan_purpose": by_purpose.to_dicts(),
        "by_state": by_state.to_dicts(),
    }


def _unused(p: Path) -> None:  # pragma: no cover - keeps Path import meaningful
    return None
