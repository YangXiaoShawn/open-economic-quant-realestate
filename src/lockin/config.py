"""Run configuration.

A run profile is a YAML file under ``configs/``. Every result artifact records the
config's SHA-256 so that a number can always be traced back to the settings that
produced it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

MortgageMode = Literal["synthetic", "registered_sample", "registered_full"]
Geography = Literal["state", "msa"]


@dataclass(slots=True)
class PathsConfig:
    raw: str = "data/raw"
    cache: str = "data/cache"
    interim: str = "data/interim"
    processed: str = "data/processed"
    fixtures: str = "data/fixtures"
    reference: str = "data/reference"
    outputs: str = "outputs"
    reports: str = "reports"


@dataclass(slots=True)
class MortgageConfig:
    mode: MortgageMode = "synthetic"
    """``synthetic`` -> labeled fixtures. ``registered_*`` -> files the user placed
    in ``data/raw/freddie`` after accepting Freddie Mac's terms."""
    cohorts: list[str] = field(default_factory=lambda: ["2020Q4", "2021Q2", "2021Q4"])
    """Origination cohorts (``YYYYQn``) to process."""
    performance_start: str = "2021-01"
    performance_end: str = "2024-12"
    fixed_rate_only: bool = True
    primary_residence_only: bool = False
    synthetic_loans_per_cohort: int = 4000
    synthetic_seed: int = 20260810
    chunk_rows: int = 500_000
    """Streaming chunk size for the performance parser."""


@dataclass(slots=True)
class RatesConfig:
    series: str = "pmms30"
    """Column of the PMMS history file. ``pmms30`` = 30-year FRM."""
    alternative_series: list[str] = field(default_factory=lambda: ["pmms15"])
    cross_check_fred: bool = True


@dataclass(slots=True)
class LockinConfig:
    thresholds_bp: list[int] = field(default_factory=lambda: [100, 200, 300, 400])
    holding_period_months: int = 84
    discount_rate_pct: float = 4.0
    refi_incentive_threshold_bp: int = 50


@dataclass(slots=True)
class SurvivalConfig:
    max_episode_rows: int = 8_000_000
    loan_sample_fraction: float = 1.0
    """<1.0 activates case-cohort sampling: all exit months are kept, non-exit
    months are sampled at this rate and carry an offset."""
    non_event_sample_fraction: float = 1.0
    sample_at_episode_build: bool = False
    """Apply the case-cohort sampling while WRITING episodes rather than after.

    Off by default: for a sample-sized run the full episode table is small and worth
    keeping, and sampling late leaves it available for other analyses. Turn it on when
    the full episode table does not fit -- on the 40-cohort Standard dataset it is
    ~522M loan-months, and materialising it exhausted the disk on a 17 GB / 228 GB
    machine. The predicate is identical either way (a stable hash of ``loan_seq_no``,
    all exit months retained), so the selected rows are the same; only the point at
    which they are selected changes."""
    age_bin_edges: list[int] = field(
        default_factory=lambda: [0, 6, 12, 18, 24, 36, 48, 60, 84, 120, 360]
    )
    out_of_time_split: str = "2024-01"
    """Episodes on/after this month form the out-of-time evaluation set."""
    seed: int = 20260810


@dataclass(slots=True)
class PanelConfig:
    geography: Geography = "state"
    states: list[str] = field(default_factory=list)
    """Empty = all states present in the loan data."""
    hpi_flavor: str = "purchase-only"
    hpi_frequency: str = "quarterly"
    """FHFA publishes purchase-only MONTHLY only at the national/division level; at
    State and MSA level purchase-only is QUARTERLY. See
    lockin.adapters.fhfa_hpi.PUBLISHED_COMBINATIONS."""
    hpi_seasonal: str = "nsa"
    hmda_years: list[int] = field(default_factory=lambda: [2018, 2019, 2020, 2021, 2022, 2023])
    permits_years: list[int] = field(default_factory=lambda: [2018, 2019, 2020, 2021, 2022, 2023])
    min_loans_per_geography: int = 50


@dataclass(slots=True)
class EventStudyConfig:
    pre_shock_date: str = "2021-12"
    shock_date: str = "2022-01"
    window_pre_periods: int = 3
    window_post_periods: int = 2
    reference_period_offset: int = -1
    exposure_measure: str = "locked_share_upb_200"
    alternative_exposures: list[str] = field(
        default_factory=lambda: [
            "locked_share_count_200",
            "locked_share_upb_100",
            "locked_share_upb_300",
            "mean_payment_gap",
        ]
    )
    placebo_shock_dates: list[str] = field(default_factory=lambda: ["2018-01", "2019-06"])
    cluster_by: str = "geography"
    outcomes: list[str] = field(
        default_factory=lambda: [
            "log_purchase_originations",
            "log_refi_originations",
            "hpi_growth",
            "log_permits_1unit",
            "log_permits_5plus",
            "denial_rate",
        ]
    )


@dataclass(slots=True)
class SimulationConfig:
    rate_shocks_bp: list[int] = field(default_factory=lambda: [-50, -100, -200])
    portability_share: float = 0.5
    assumability_share: float = 0.3
    seller_credit_dollars: float = 10_000.0
    buydown_bp: int = 100
    supply_elasticity_multiplier: float = 1.5
    price_elasticity_of_demand: float = -0.6
    """CALIBRATED, not estimated. Recorded in every scenario artifact."""
    supply_elasticity: float = 1.0
    """CALIBRATED, not estimated."""


@dataclass(slots=True)
class Config:
    name: str = "sample"
    label: str = "SYNTHETIC loan fixtures + official public aggregate series"
    paths: PathsConfig = field(default_factory=PathsConfig)
    mortgage: MortgageConfig = field(default_factory=MortgageConfig)
    rates: RatesConfig = field(default_factory=RatesConfig)
    lockin: LockinConfig = field(default_factory=LockinConfig)
    survival: SurvivalConfig = field(default_factory=SurvivalConfig)
    panel: PanelConfig = field(default_factory=PanelConfig)
    event_study: EventStudyConfig = field(default_factory=EventStudyConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    offline: bool = False
    """If True, adapters must use only what is already in ``data/cache``."""

    # -- derived -----------------------------------------------------------
    @property
    def data_class(self) -> str:
        """``SYNTHETIC`` or ``REGISTERED``. Drives report banners."""
        return "SYNTHETIC" if self.mortgage.mode == "synthetic" else "REGISTERED"

    @property
    def manifest_data_class(self) -> str:
        """The same fact in the manifest's vocabulary.

        Manifests classify data by *redistribution status* -- ``PUBLIC``, ``RESTRICTED``,
        ``SYNTHETIC``, ``DERIVED`` -- while reports and the CLI say ``REGISTERED``,
        which describes how it was obtained. For the Freddie Mac loan-level dataset the
        two coincide: it is behind registration precisely because its licence forbids
        redistribution, so it is ``RESTRICTED``.

        Kept as a separate property rather than by widening the manifest vocabulary,
        because a manifest reader asking "may I republish this?" should not have to know
        that ``REGISTERED`` implies no.
        """
        return "RESTRICTED" if self.data_class == "REGISTERED" else self.data_class

    def path(self, key: str, *parts: str) -> Path:
        base = REPO_ROOT / getattr(self.paths, key)
        p = base.joinpath(*parts)
        return p

    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, default=str).encode()
        return hashlib.sha256(payload).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _merge(dc: Any, overrides: dict[str, Any]) -> None:
    """Recursively apply a YAML mapping onto a dataclass instance."""
    for key, value in overrides.items():
        if not hasattr(dc, key):
            raise KeyError(f"unknown config key: {key!r} (on {type(dc).__name__})")
        current = getattr(dc, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _merge(current, value)
        else:
            setattr(dc, key, value)


def load_config(path: str | Path | None = None) -> Config:
    """Load a run profile. ``None`` returns the built-in defaults."""
    cfg = Config()
    if path is None:
        return cfg
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")
    raw = yaml.safe_load(p.read_text()) or {}
    _merge(cfg, raw)
    return cfg
