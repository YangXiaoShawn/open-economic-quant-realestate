"""Pipeline status: which stages have run, and what they produced.

Backs both ``lockin status`` and the dashboard's pipeline panel.
"""

from __future__ import annotations

from typing import Any

from lockin.artifacts import list_artifacts, try_read_artifact
from lockin.config import Config
from lockin.manifest import read_manifest


def _stage(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"stage": name, "ok": ok, "detail": detail}


def pipeline_status(cfg: Config) -> dict[str, Any]:
    stages: list[dict[str, Any]] = []

    # -- public data ---------------------------------------------------------
    for src, fname in (
        ("pmms", "PMMS_history.csv"),
        ("fhfa_hpi", "hpi_master.csv"),
        ("hmda", "hmda_state_year_aggregates.parquet"),
        ("census_bps", "bps_state_monthly.parquet"),
    ):
        p = cfg.path("cache", src, fname)
        if p.exists():
            try:
                m = read_manifest(p)
                stages.append(
                    _stage(
                        f"fetch:{src}",
                        True,
                        f"{m['row_count']:,} rows · {m['coverage_period']} · "
                        f"retrieved {m['retrieved_at']}",
                    )
                )
            except FileNotFoundError:
                stages.append(_stage(f"fetch:{src}", False, "file present but no manifest"))
        else:
            stages.append(_stage(f"fetch:{src}", False, f"missing {p.name}"))

    # -- loan data -----------------------------------------------------------
    fixtures = cfg.path("fixtures", "freddie")
    raw = cfg.path("raw", "freddie")
    n_fix = len(list(fixtures.glob("*.txt"))) if fixtures.exists() else 0
    n_raw = len(list(raw.glob("*.zip"))) + len(list(raw.glob("*.txt"))) if raw.exists() else 0
    stages.append(
        _stage(
            "loan source",
            n_fix > 0 or n_raw > 0,
            f"{n_raw} registered archive(s) in data/raw/freddie, {n_fix} SYNTHETIC fixture file(s)"
            + (" — using REGISTERED data" if n_raw else " — using SYNTHETIC fixtures"),
        )
    )

    for label, key, parts in (
        ("ingest:origination", "interim", ("origination",)),
        ("ingest:performance", "interim", ("performance",)),
        ("build-lockin:episodes", "processed", ("loan_episodes",)),
    ):
        d = cfg.path(key, *parts)
        if d.exists():
            try:
                m = read_manifest(d)
                stages.append(_stage(label, True, f"{m['row_count']:,} rows · {m['data_class']}"))
            except FileNotFoundError:
                stages.append(_stage(label, False, "directory present but no manifest"))
        else:
            stages.append(_stage(label, False, "not built"))

    for label, fname in (
        ("build-loan-events", "loan_events.parquet"),
        ("active stock", f"active_stock_{cfg.panel.geography}.parquet"),
        ("local panel (monthly)", "local_market_panel_monthly.parquet"),
        ("local panel (annual)", "local_market_panel_annual.parquet"),
    ):
        p = cfg.path("processed", fname)
        if p.exists():
            try:
                m = read_manifest(p)
                stages.append(_stage(label, True, f"{m['row_count']:,} rows · {m['data_class']}"))
            except FileNotFoundError:
                stages.append(_stage(label, False, "file present but no manifest"))
        else:
            stages.append(_stage(label, False, "not built"))

    # -- estimation outputs --------------------------------------------------
    for label, group, name in (
        ("estimate-hazards", "hazards", "dt_logit_prepayment"),
        ("estimate-local-effects", "eventstudy", "es_log_purchase_originations"),
        ("robustness", "robustness", "robustness_grid"),
        ("benchmark", "benchmark", "benchmark_comparison"),
        ("simulate-policy", "scenarios", "scenario_comparison"),
        ("validate-data", "validation", "validation_report"),
    ):
        art = try_read_artifact(cfg, group, name)
        if art is None:
            stages.append(_stage(label, False, f"artifact {group}/{name} missing"))
        else:
            stages.append(
                _stage(
                    label,
                    True,
                    f"tier `{art['evidence_tier']}` · {art['provenance']['data_class']} · "
                    f"commit {art['provenance']['git_commit']}",
                )
            )

    reports = (
        sorted(p.name for p in cfg.path("reports").glob("*.md"))
        if cfg.path("reports").exists()
        else []
    )
    stages.append(
        _stage("report", len(reports) > 0, f"{len(reports)} report(s): " + ", ".join(reports[:12]))
    )

    arts = list_artifacts(cfg)
    tiers: dict[str, int] = {}
    for a in arts:
        art = try_read_artifact(cfg, a.parent.name, a.stem)
        if art:
            tiers[art["evidence_tier"]] = tiers.get(art["evidence_tier"], 0) + 1

    return {
        "config": cfg.name,
        "config_digest": cfg.digest(),
        "data_class": cfg.data_class,
        "n_artifacts": len(arts),
        "artifacts_by_tier": tiers,
        "n_stages_ok": sum(1 for s in stages if s["ok"]),
        "n_stages": len(stages),
        "stages": stages,
    }
