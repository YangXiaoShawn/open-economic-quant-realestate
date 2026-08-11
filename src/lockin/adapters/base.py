"""Adapter base: cached HTTP fetch with manifests and loud failures.

Design rules for every adapter in this package:

* **Replaceable.** Each adapter exposes ``fetch()`` (network -> cache + manifest)
  and ``load()`` (cache -> Polars DataFrame). Nothing else in the codebase touches
  a URL.
* **Idempotent.** ``fetch()`` is a no-op if the cached file is younger than
  ``max_age_days`` and its checksum matches its manifest.
* **Loud.** A dead URL raises with every URL tried, not a silent fallback to stale
  data. Upstream URLs move (the PMMS one already has).
* **Offline-capable.** ``cfg.offline = True`` forbids network access; ``load()``
  then requires the cache to already be populated.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

from lockin.config import Config
from lockin.manifest import read_manifest

USER_AGENT = "lockin-research/0.1 (academic housing-finance research; contact: repository owner)"

DEFAULT_TIMEOUT = 120


class AdapterError(RuntimeError):
    """Raised when an adapter cannot obtain the data it needs."""


class OfflineError(AdapterError):
    """Raised when the config forbids network access and the cache is cold."""


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Static description of a data source, used to build its manifest."""

    name: str
    source: str
    urls: tuple[str, ...]
    license_terms: str
    redistribution_status: str
    geographic_level: str
    known_limitations: tuple[str, ...]
    data_class: str = "PUBLIC"
    schema_version: str = "v1"


def cache_path(cfg: Config, source: str, filename: str) -> Path:
    p = cfg.path("cache", source, filename)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _is_fresh(target: Path, max_age_days: int) -> bool:
    if not target.exists():
        return False
    try:
        m = read_manifest(target)
    except FileNotFoundError:
        return False
    try:
        retrieved = datetime.fromisoformat(str(m["retrieved_at"]))
    except (KeyError, ValueError):
        return False
    if retrieved.tzinfo is None:
        retrieved = retrieved.replace(tzinfo=UTC)
    return datetime.now(UTC) - retrieved < timedelta(days=max_age_days)


def download(
    cfg: Config,
    urls: tuple[str, ...] | list[str],
    target: Path,
    *,
    max_age_days: int = 7,
    expect_text: bool = True,
    min_bytes: int = 256,
    retries: int = 3,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[Path, str]:
    """Fetch the first URL that works into ``target``. Returns ``(path, url_used)``.

    Raises :class:`AdapterError` listing every URL tried if all fail, and
    :class:`OfflineError` if ``cfg.offline`` and the cache is cold.
    """
    if _is_fresh(target, max_age_days):
        m = read_manifest(target)
        return (target, str(m.get("source_url", urls[0])))

    if cfg.offline:
        if target.exists():
            return (target, "cache(offline)")
        raise OfflineError(
            f"offline=True and no cached copy at {target}. "
            f"Run `make fetch-public-data` with network access first."
        )

    failures: list[str] = []
    for url in urls:
        for attempt in range(retries):
            try:
                resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
                if resp.status_code != 200:
                    failures.append(f"{url} -> HTTP {resp.status_code}")
                    break
                body = resp.content
                if len(body) < min_bytes:
                    failures.append(f"{url} -> only {len(body)} bytes")
                    break
                if expect_text and body.lstrip()[:15].lower().startswith(b"<!doctype html"):
                    failures.append(f"{url} -> HTML error page, not data")
                    break
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
                return (target, url)
            except requests.RequestException as exc:
                failures.append(f"{url} -> {type(exc).__name__}: {exc}")
                if attempt < retries - 1:
                    time.sleep(1.5 * (attempt + 1))

    if target.exists():
        raise AdapterError(
            "all URLs failed; a STALE cached copy exists at "
            f"{target}. Refusing to silently use it. Tried:\n  " + "\n  ".join(failures)
        )
    raise AdapterError("all URLs failed. Tried:\n  " + "\n  ".join(failures))
