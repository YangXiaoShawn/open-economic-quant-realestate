"""Which run profile produced the data sitting in ``data/interim`` and ``data/processed``.

``data/interim`` and ``data/processed`` are shared paths. Nothing about them records
*which* profile wrote them, so running ``--config configs/sample.yaml`` after a
``configs/full.yaml`` ingest reads the full dataset while every artifact is stamped with
the sample profile's digest, its cohorts, and -- worst of all -- its ``SYNTHETIC`` data
class. A synthetic-labelled artifact computed from registered loan-level records is a
licence problem as well as a provenance one.

It is not hypothetical: it happened during the first full-set ingest in this repository.
A sample-config command was pointed at freshly written full-set interim tables and began
a global sort over 522 million rows, which spilled ~20 GB of scratch and took the disk
from 26 GB free to 4.3 GB before it was killed.

So each writer drops a stamp, and each reader checks it. The check is cheap, and the
failure it prevents is silent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lockin.config import Config

STAMP_NAME = ".lockin_profile.json"


class ProfileMismatchError(RuntimeError):
    """Data on disk was written by a different run profile than the one reading it.

    Deliberately fatal. The alternative -- carrying on -- produces numbers whose
    provenance envelope describes a population that did not generate them.
    """


def _payload(cfg: Config) -> dict[str, Any]:
    return {
        "profile": cfg.name,
        "mortgage_mode": cfg.mortgage.mode,
        "data_class": cfg.data_class,
        "cohorts": list(cfg.mortgage.cohorts),
        "performance_start": cfg.mortgage.performance_start,
        "performance_end": cfg.mortgage.performance_end,
        "config_digest": cfg.digest(),
    }


def write(cfg: Config, directory: Path) -> Path:
    """Record which profile wrote ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / STAMP_NAME
    p.write_text(json.dumps(_payload(cfg), indent=2, sort_keys=True))
    return p


def read(directory: Path) -> dict[str, Any] | None:
    p = directory / STAMP_NAME
    if not p.exists():
        return None
    try:
        return dict(json.loads(p.read_text()))
    except (OSError, json.JSONDecodeError):
        return None


def check(cfg: Config, directory: Path) -> None:
    """Raise if ``directory`` was written by an incompatible profile.

    An **unstamped** directory is allowed through with no error: data written before
    stamping existed, or by hand, is not evidence of a mismatch. Only a stamp that
    actively disagrees is fatal.

    The comparison is on ``mortgage_mode`` and ``data_class`` rather than the profile
    name, because two profiles that agree on both are genuinely interchangeable readers
    of the same loan-level tables -- a state-level and an MSA-level profile over the same
    registered ingest, say. Mixing SYNTHETIC and REGISTERED is what must never happen.
    """
    stamp = read(directory)
    if stamp is None:
        return
    if (
        stamp.get("data_class") == cfg.data_class
        and stamp.get("mortgage_mode") == cfg.mortgage.mode
    ):
        return
    raise ProfileMismatchError(
        f"{directory} was written by profile {stamp.get('profile')!r} "
        f"(mode={stamp.get('mortgage_mode')!r}, data_class={stamp.get('data_class')!r}), but "
        f"you are reading it as {cfg.name!r} (mode={cfg.mortgage.mode!r}, "
        f"data_class={cfg.data_class!r}).\n\n"
        "Reading it anyway would stamp artifacts with this profile's provenance while the "
        "numbers came from the other profile's population -- and would label registered "
        "loan-level records as SYNTHETIC, or the reverse.\n\n"
        "Re-run the ingest for this profile, or point the profile at the mode that "
        f"produced the data (cohorts on disk: {stamp.get('cohorts')})."
    )
