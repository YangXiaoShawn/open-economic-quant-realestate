"""Result artifacts.

A *result artifact* is the only legitimate source of a number in a report
(acceptance criterion 12 of the research principles: every empirical claim must be
traceable to a reproducible result artifact).

An artifact is a JSON file under ``outputs/<group>/<name>.json`` with a fixed
envelope::

    {
      "artifact": "<name>",
      "group": "<group>",
      "evidence_tier": "descriptive" | "hazard_association"
                       | "quasi_experimental" | "simulation",
      "population": "...",
      "geography": "...",
      "outcome_definition": "...",
      "weight": "...",
      "provenance": { ...RunContext... },
      "result": { ...payload... }
    }

``write_artifact`` refuses to write without a tier, a population statement, and a
run context.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from lockin.config import Config
from lockin.provenance import EVIDENCE_TIERS, RunContext


def _jsonable(obj: Any) -> Any:
    """Convert numpy / Path / set values into JSON-serialisable equivalents."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(_jsonable(v) for v in obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        f = float(obj)
        return None if (np.isnan(f) or np.isinf(f)) else f
    if isinstance(obj, np.ndarray):
        return _jsonable(obj.tolist())
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, float):
        return None if (np.isnan(obj) or np.isinf(obj)) else obj
    if hasattr(obj, "to_dict"):
        return _jsonable(obj.to_dict())
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict

        return _jsonable(asdict(obj))
    return obj


def write_artifact(
    cfg: Config,
    ctx: RunContext,
    *,
    group: str,
    name: str,
    evidence_tier: str,
    population: str,
    geography: str,
    outcome_definition: str,
    weight: str,
    result: dict[str, Any],
    caveats: list[str] | None = None,
) -> Path:
    """Write one result artifact and return its path."""
    if evidence_tier not in EVIDENCE_TIERS:
        raise ValueError(f"evidence_tier must be one of {EVIDENCE_TIERS}, got {evidence_tier!r}")
    if not population.strip():
        raise ValueError("population statement is mandatory on every artifact")

    envelope = {
        "artifact": name,
        "group": group,
        "evidence_tier": evidence_tier,
        "population": population,
        "geography": geography,
        "outcome_definition": outcome_definition,
        "weight": weight,
        "caveats": caveats or [],
        "provenance": ctx.to_dict(),
        "result": _jsonable(result),
    }
    out_dir = cfg.path("outputs", group)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{name}.json"
    p.write_text(json.dumps(envelope, indent=2, sort_keys=False) + "\n")
    return p


def read_artifact(cfg: Config, group: str, name: str) -> dict[str, Any]:
    p = cfg.path("outputs", group, f"{name}.json")
    if not p.exists():
        raise FileNotFoundError(
            f"missing artifact {group}/{name}. Run the corresponding make target first."
        )
    data: dict[str, Any] = json.loads(p.read_text())
    return data


def try_read_artifact(cfg: Config, group: str, name: str) -> dict[str, Any] | None:
    try:
        return read_artifact(cfg, group, name)
    except FileNotFoundError:
        return None


def list_artifacts(cfg: Config, group: str | None = None) -> list[Path]:
    base = cfg.path("outputs") if group is None else cfg.path("outputs", group)
    if not base.exists():
        return []
    return sorted(base.rglob("*.json"))
