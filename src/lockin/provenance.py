"""Run provenance.

Acceptance criterion 21: *every result records data period, configuration, source
version, and Git commit*. This module is the single mechanism that satisfies it.
``lockin.artifacts.write_artifact`` refuses to write anything without a
``RunContext``.
"""

from __future__ import annotations

import getpass
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from lockin.config import REPO_ROOT, Config

EvidenceTier = Literal["descriptive", "hazard_association", "quasi_experimental", "simulation"]

EVIDENCE_TIERS: tuple[EvidenceTier, ...] = (
    "descriptive",
    "hazard_association",
    "quasi_experimental",
    "simulation",
)

#: Language permitted at each tier. Enforced by ``lockin.reporting.render``.
TIER_LANGUAGE: dict[str, str] = {
    "descriptive": "describes / shows / among ... the rate was",
    "hazard_association": "is associated with / predicts / conditional on",
    "quasi_experimental": "reduced / increased (only if pre-trends and placebos pass)",
    "simulation": "under the model / model-dependent / not a forecast",
}


def git_commit() -> str:
    """Current HEAD, with a ``-dirty`` suffix if the tree has uncommitted changes."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if sha.returncode != 0:
            return "no-commit"
        head = sha.stdout.strip()[:12]
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return f"{head}-dirty" if status.stdout.strip() else head
    except (OSError, subprocess.SubprocessError):
        return "unknown"


@dataclass(slots=True)
class RunContext:
    """Attached to every result artifact."""

    run_timestamp: str
    git_commit: str
    config_name: str
    config_digest: str
    data_class: str
    """``SYNTHETIC`` or ``REGISTERED``."""
    data_period: str
    """Coverage of the inputs actually used, e.g. ``2021-01..2024-12``."""
    source_versions: dict[str, str] = field(default_factory=dict)
    """source name -> schema/release/retrieval identifier."""
    python_version: str = field(default_factory=lambda: sys.version.split()[0])
    platform_str: str = field(default_factory=lambda: f"{platform.system()}-{platform.machine()}")
    user: str = field(default_factory=lambda: _safe_user())
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def run_context(
    cfg: Config,
    data_period: str | None = None,
    source_versions: dict[str, str] | None = None,
    notes: list[str] | None = None,
) -> RunContext:
    """Build a :class:`RunContext` for the current run."""
    period = data_period or f"{cfg.mortgage.performance_start}..{cfg.mortgage.performance_end}"
    return RunContext(
        run_timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
        git_commit=git_commit(),
        config_name=cfg.name,
        config_digest=cfg.digest(),
        data_class=cfg.data_class,
        data_period=period,
        source_versions=source_versions or {},
        notes=notes or [],
    )


def collect_source_versions(cfg: Config) -> dict[str, str]:
    """Read every manifest in the cache/fixtures tree into a version map."""
    from lockin.manifest import read_manifest  # local import: avoid a cycle

    out: dict[str, str] = {}
    for base_key in ("cache", "fixtures", "processed"):
        base: Path = cfg.path(base_key)
        if not base.exists():
            continue
        for mf in sorted(base.rglob("*.manifest.json")):
            try:
                m = read_manifest(mf)
            except Exception:
                out[mf.stem] = "unreadable-manifest"
                continue
            key = m.get("name", mf.stem)
            out[str(key)] = (
                f"{m.get('schema_version', '?')}"
                f"@{m.get('retrieved_at', '?')}"
                f"#{str(m.get('checksum_sha256', '?'))[:12]}"
            )
    return out
