"""Validation runner: schemas, checksums, coverage, and internal consistency.

Problems are prefixed by severity so callers can act on them:

* ``HARD`` -- a defect that invalidates downstream results. ``make validate-data``
  exits non-zero.
* ``SOFT`` -- a data-quality quirk that is expected in real files and is recorded
  rather than fixed silently.
* ``INFO`` -- a fact worth reporting (share left truncated, attrition, coverage).
"""

from __future__ import annotations

from typing import Any

import polars as pl

from lockin.artifacts import write_artifact
from lockin.config import Config
from lockin.manifest import verify_manifest
from lockin.provenance import collect_source_versions, run_context


def _manifest_checks(cfg: Config) -> list[str]:
    problems: list[str] = []
    checked = 0
    for key in ("cache", "fixtures", "interim", "processed"):
        base = cfg.path(key)
        if not base.exists():
            continue
        for mf in sorted(base.rglob("*.manifest.json")):
            target = (
                mf.parent
                if mf.name.startswith("_dataset")
                else mf.with_name(mf.name.replace(".manifest.json", ""))
            )
            if not target.exists():
                problems.append(f"HARD: manifest {mf.name} has no data target at {target}")
                continue
            ok, msg = verify_manifest(target)
            checked += 1
            if not ok:
                problems.append(f"HARD: {msg}")
    problems.append(f"INFO: verified {checked} dataset manifest(s)")
    return problems


def _schema_checks() -> list[str]:
    from lockin.schemas.freddie import assert_layout_verified

    try:
        assert_layout_verified()
        return ["INFO: Freddie Mac layout invariants hold (32 + 32 fields, ZB priorities 1..7)"]
    except AssertionError as exc:
        return [f"HARD: schema invariant failed: {exc}"]


def _rate_checks(cfg: Config) -> list[str]:
    from lockin.adapters import pmms
    from lockin.rates import assert_no_look_ahead, monthly_market_rate

    problems: list[str] = []
    try:
        raw = pmms.load(cfg)
    except FileNotFoundError as exc:
        return [f"HARD: {exc}"]

    monthly = monthly_market_rate(raw, series=cfg.rates.series)
    try:
        assert_no_look_ahead(monthly)
        problems.append(
            f"INFO: point-in-time alignment verified for {cfg.rates.series} over "
            f"{monthly.height} months (no look-ahead)"
        )
    except AssertionError as exc:
        problems.append(f"HARD: {exc}")

    regimes = (
        monthly.group_by("methodology_regime").agg(pl.len().alias("n")).sort("n", descending=True)
    )
    problems.append(
        "INFO: PMMS methodology regimes in the aligned series: "
        + ", ".join(f"{r['methodology_regime']}={r['n']}" for r in regimes.to_dicts())
    )

    # Cross-check against FRED where available.
    try:
        fred = pmms.fetch_fred_cross_check(cfg)
        merged = raw.select("date", cfg.rates.series).join(fred, on="date", how="inner")
        if merged.height:
            diff = (merged[cfg.rates.series].cast(pl.Float64) - merged["fred_mortgage30us"]).abs()
            worst = float(diff.max() or 0.0)
            if worst > 0.02:
                problems.append(
                    f"SOFT: PMMS vs FRED MORTGAGE30US differ by up to {worst:.3f} pp "
                    f"over {merged.height} shared weeks"
                )
            else:
                problems.append(
                    f"INFO: PMMS agrees with FRED MORTGAGE30US to within "
                    f"{worst:.3f} pp over {merged.height} shared weeks"
                )
    except Exception as exc:
        problems.append(f"INFO: FRED cross-check unavailable ({type(exc).__name__})")
    return problems


def _governance_checks(cfg: Config) -> list[str]:
    """No restricted or bulk data may be tracked by git."""
    import subprocess

    from lockin.config import REPO_ROOT

    problems: list[str] = []
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=20
        )
        tracked = [ln for ln in out.stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        return ["INFO: git unavailable; governance check skipped"]

    banned_prefixes = ("data/raw/", "data/cache/", "data/interim/", "data/processed/", "outputs/")
    banned_suffixes = (".parquet", ".zip", ".gz")
    for f in tracked:
        if f.startswith(banned_prefixes):
            problems.append(f"HARD: restricted-path file is tracked by git: {f}")
        if f.endswith(banned_suffixes):
            problems.append(f"HARD: bulk-data file is tracked by git: {f}")
        p = REPO_ROOT / f
        if p.exists() and p.stat().st_size > 5_000_000:
            problems.append(f"HARD: tracked file exceeds 5 MB: {f} ({p.stat().st_size:,} bytes)")
    problems.append(f"INFO: governance scan over {len(tracked)} tracked files")
    _ = cfg
    return problems


def run_all_validations(cfg: Config) -> dict[str, Any]:
    """Run every validation section and write a validation artifact."""
    from lockin.episodes import validate_episodes
    from lockin.events import load_loan_events, validate_events
    from lockin.ingest import origination as orig_mod
    from lockin.ingest import performance as perf_mod

    sections: dict[str, list[str]] = {}
    sections["schema"] = _schema_checks()
    sections["manifests"] = _manifest_checks(cfg)
    sections["market_rates"] = _rate_checks(cfg)
    sections["governance"] = _governance_checks(cfg)

    for label, fn in (
        ("origination", lambda: orig_mod.validate(cfg)),
        ("performance", lambda: perf_mod.validate(cfg)),
    ):
        try:
            sections[label] = fn()
        except FileNotFoundError as exc:
            sections[label] = [f"HARD: {exc}"]

    try:
        sections["loan_events"] = validate_events(load_loan_events(cfg))
    except FileNotFoundError as exc:
        sections["loan_events"] = [f"HARD: {exc}"]

    try:
        sections["episodes"] = validate_episodes(cfg)
    except FileNotFoundError as exc:
        sections["episodes"] = [f"HARD: {exc}"]

    try:
        from lockin.stock import coverage_summary, load_active_stock

        cov = coverage_summary(load_active_stock(cfg))
        sections["coverage"] = [
            f"INFO: active stock {cov.get('n_active_first')} loans at "
            f"{cov.get('first_period')}, peaking at {cov.get('n_active_peak')} at "
            f"{cov.get('peak_period')}, ending at {cov.get('n_active_last')} at "
            f"{cov.get('last_period')} across {cov.get('n_geographies')} geographies",
            f"INFO: attrition from peak {cov.get('attrition_from_peak_pct')}%; net change "
            f"first-to-last {cov.get('net_change_first_to_last_pct')}% (the stock has BOTH "
            "entry and exit -- see the coverage interpretation in the manifest)",
        ]
    except FileNotFoundError:
        sections["coverage"] = ["INFO: active stock not built yet"]

    flat = [p for ps in sections.values() for p in ps]
    n_hard = sum(1 for p in flat if p.startswith("HARD"))
    n_soft = sum(1 for p in flat if p.startswith("SOFT"))
    n_info = sum(1 for p in flat if p.startswith("INFO"))

    result = {
        "sections": sections,
        "n_hard": n_hard,
        "n_soft": n_soft,
        "n_info": n_info,
        "severity_meaning": {
            "HARD": "invalidates downstream results; the pipeline exits non-zero",
            "SOFT": "a data-quality quirk expected in real files; recorded, not silently fixed",
            "INFO": "a fact worth reporting (truncation share, attrition, coverage)",
        },
    }
    ctx = run_context(cfg, source_versions=collect_source_versions(cfg))
    write_artifact(
        cfg,
        ctx,
        group="validation",
        name="validation_report",
        evidence_tier="descriptive",
        population="all ingested tables and cached public series",
        geography=cfg.panel.geography,
        outcome_definition="n/a (data validation)",
        weight="n/a",
        result=result,
        caveats=["SOFT findings are expected in real Freddie Mac files and are not bugs."],
    )
    return result
