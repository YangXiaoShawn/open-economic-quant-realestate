"""Dataset manifests.

Every dataset written to disk gets a sidecar ``<name>.manifest.json`` carrying the
fields required by the data-governance policy: source, retrieval timestamp,
release date, coverage period, licence/terms, redistribution status, schema
version, row count, checksum, geographic level, and known limitations.

``make validate-data`` fails when a manifest is missing or a checksum mismatches.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MANIFEST_SUFFIX = ".manifest.json"

REQUIRED_KEYS: tuple[str, ...] = (
    "name",
    "source",
    "source_url",
    "retrieved_at",
    "release_date",
    "coverage_period",
    "license_terms",
    "redistribution_status",
    "schema_version",
    "row_count",
    "checksum_sha256",
    "geographic_level",
    "known_limitations",
    "data_class",
)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Streaming SHA-256 so that multi-GB files do not need to fit in memory."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


#: Sidecar files that describe a dataset directory rather than being part of it.
#: They must be excluded from the directory digest, or writing one would invalidate
#: the checksum of the data it describes.
_NON_DATA_SIDECARS: tuple[str, ...] = (MANIFEST_SUFFIX, ".lockin_profile.json")


def _is_data_file(x: Path) -> bool:
    return x.is_file() and not any(tag in x.name for tag in _NON_DATA_SIDECARS)


def sha256_dir(path: Path, pattern: str = "**/*") -> str:
    """Order-stable digest over a directory tree (for partitioned Parquet)."""
    h = hashlib.sha256()
    for p in sorted(x for x in path.glob(pattern) if _is_data_file(x)):
        h.update(p.relative_to(path).as_posix().encode())
        h.update(sha256_file(p).encode())
    return h.hexdigest()


def manifest_path(target: Path) -> Path:
    """Sidecar path for a file or a directory dataset."""
    if target.is_dir():
        return target / ("_dataset" + MANIFEST_SUFFIX)
    return target.with_suffix(target.suffix + MANIFEST_SUFFIX)


def write_manifest(
    target: Path,
    *,
    name: str,
    source: str,
    source_url: str,
    license_terms: str,
    redistribution_status: str,
    schema_version: str,
    row_count: int,
    geographic_level: str,
    coverage_period: str,
    known_limitations: list[str],
    data_class: str = "PUBLIC",
    release_date: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write the sidecar manifest for ``target`` and return its path.

    ``data_class`` must be one of ``PUBLIC``, ``RESTRICTED``, ``SYNTHETIC``,
    ``DERIVED``. ``SYNTHETIC`` propagates into report banners.
    """
    if data_class not in {"PUBLIC", "RESTRICTED", "SYNTHETIC", "DERIVED"}:
        raise ValueError(f"bad data_class: {data_class!r}")
    if not target.exists():
        raise FileNotFoundError(target)

    checksum = sha256_dir(target) if target.is_dir() else sha256_file(target)
    payload: dict[str, Any] = {
        "name": name,
        "source": source,
        "source_url": source_url,
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "release_date": release_date or "not-published-by-source",
        "coverage_period": coverage_period,
        "license_terms": license_terms,
        "redistribution_status": redistribution_status,
        "schema_version": schema_version,
        "row_count": int(row_count),
        "checksum_sha256": checksum,
        "checksum_scope": "directory" if target.is_dir() else "file",
        "geographic_level": geographic_level,
        "known_limitations": known_limitations,
        "data_class": data_class,
        "manifest_version": 1,
    }
    if extra:
        payload["extra"] = extra

    missing = [k for k in REQUIRED_KEYS if k not in payload]
    if missing:
        raise ValueError(f"manifest missing required keys: {missing}")

    mp = manifest_path(target)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return mp


def read_manifest(path: Path) -> dict[str, Any]:
    """Read a manifest given either the manifest path or its data target."""
    p = path if path.name.endswith(MANIFEST_SUFFIX) else manifest_path(path)
    if not p.exists():
        raise FileNotFoundError(f"no manifest at {p}")
    data: dict[str, Any] = json.loads(p.read_text())
    return data


def verify_manifest(target: Path) -> tuple[bool, str]:
    """Re-checksum ``target`` and compare with its manifest.

    Returns ``(ok, message)``.
    """
    try:
        m = read_manifest(target)
    except FileNotFoundError as exc:
        return (False, str(exc))
    actual = sha256_dir(target) if target.is_dir() else sha256_file(target)
    if actual != m.get("checksum_sha256"):
        return (
            False,
            f"checksum mismatch for {target.name}: manifest says "
            f"{str(m.get('checksum_sha256'))[:12]}, file is {actual[:12]}",
        )
    return (True, f"{target.name}: ok ({m['row_count']:,} rows, {m['data_class']})")


def any_synthetic(manifests: list[dict[str, Any]]) -> bool:
    """True if any input was synthetic -- drives the mandatory report banner."""
    return any(m.get("data_class") == "SYNTHETIC" for m in manifests)
