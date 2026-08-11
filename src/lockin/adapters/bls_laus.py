"""BLS Local Area Unemployment Statistics (LAUS) adapter -- **optional**.

Supplies the state unemployment rate, which is the control that moves the
"local labour-market shocks" threat in ``docs/IDENTIFICATION_STRATEGY.md`` §3.4 from
*unresolved* to *controlled*. Local labour conditions move both mortgage exits and
purchase originations independently of lock-in, so leaving it out is a real omission
rather than a nicety.

**Optional by design.** The whole pipeline runs without it: the panel builder joins it
when present and records its absence when not, and the event study adds it to the
control set only if the column exists. Nothing raises.

Access route, and why this one:

* The BLS **bulk flat files** at ``download.bls.gov/pub/time.series/la/`` return
  **HTTP 403** to a generic client. BLS asks automated downloaders to identify
  themselves with a contact email address. We do not put the user's personal email
  into an outbound header without being asked to, so we do not use this route.
* The BLS **public JSON API v2** serves the same series without a registration key,
  subject to published limits for unregistered use (on the order of 25 queries per
  day, 25 series and 20 years per query). Three queries cover all 51 state series,
  which is comfortably inside that. This is the route we use.

Series identifier: ``LASST{fips}0000000000003`` -- LAUS, **S**tate, **S**easonally
adjusted, measure ``03`` = unemployment **rate**.
"""

from __future__ import annotations

import json
from typing import Any, Final

import polars as pl
import requests

from lockin.adapters.base import USER_AGENT, AdapterError, OfflineError, SourceSpec, cache_path
from lockin.config import Config
from lockin.manifest import write_manifest

API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

#: Unregistered-use limits published by BLS for API v2.
MAX_SERIES_PER_QUERY: Final[int] = 25
MAX_YEARS_PER_QUERY: Final[int] = 20

SPEC = SourceSpec(
    name="bls_laus",
    source="U.S. Bureau of Labor Statistics, Local Area Unemployment Statistics (LAUS)",
    urls=(API,),
    license_terms=(
        "U.S. Government work; public domain. BLS asks that automated users identify "
        "themselves and stay within the published request limits for unregistered use."
    ),
    redistribution_status="public domain; cached API responses not committed",
    geographic_level="state",
    known_limitations=(
        "MODEL-BASED estimates, not a direct count. State LAUS estimates come from a "
        "signal-plus-noise model using CPS, CES, and UI claims inputs -- they are not "
        "a survey count of the unemployed in that state.",
        "Subject to substantial ANNUAL REVISION and to periodic re-benchmarking; the "
        "revision footnotes returned by the API are preserved in the cache.",
        "The unemployment RATE conflates changes in employment with changes in labour "
        "force participation. A falling rate can mean people found work or stopped "
        "looking.",
        "State-level averaging hides within-state variation that is often larger than "
        "the between-state variation the panel exploits.",
        "Seasonally adjusted series are used; the adjustment itself is revised.",
        "Unregistered API use is rate-limited, so a failed fetch is expected "
        "occasionally and is handled as an optional-source absence, not an error.",
    ),
    schema_version="bls-api-v2-laus-state-v1",
)

#: state abbreviation -> FIPS, for building series identifiers.
STATE_FIPS: Final[dict[str, str]] = {
    "AL": "01",
    "AK": "02",
    "AZ": "04",
    "AR": "05",
    "CA": "06",
    "CO": "08",
    "CT": "09",
    "DE": "10",
    "DC": "11",
    "FL": "12",
    "GA": "13",
    "HI": "15",
    "ID": "16",
    "IL": "17",
    "IN": "18",
    "IA": "19",
    "KS": "20",
    "KY": "21",
    "LA": "22",
    "ME": "23",
    "MD": "24",
    "MA": "25",
    "MI": "26",
    "MN": "27",
    "MS": "28",
    "MO": "29",
    "MT": "30",
    "NE": "31",
    "NV": "32",
    "NH": "33",
    "NJ": "34",
    "NM": "35",
    "NY": "36",
    "NC": "37",
    "ND": "38",
    "OH": "39",
    "OK": "40",
    "OR": "41",
    "PA": "42",
    "RI": "44",
    "SC": "45",
    "SD": "46",
    "TN": "47",
    "TX": "48",
    "UT": "49",
    "VT": "50",
    "VA": "51",
    "WA": "53",
    "WV": "54",
    "WI": "55",
    "WY": "56",
}
_FIPS_TO_STATE: Final[dict[str, str]] = {v: k for k, v in STATE_FIPS.items()}

FILENAME = "laus_state_monthly.parquet"


def series_id(state: str) -> str:
    """LAUS seasonally adjusted state unemployment-rate series identifier."""
    fips = STATE_FIPS[state.upper()]
    return f"LASST{fips}0000000000003"


def _state_from_series(sid: str) -> str | None:
    return _FIPS_TO_STATE.get(sid[5:7]) if sid.startswith("LASST") else None


def fetch(
    cfg: Config,
    years: list[int] | None = None,
    states: list[str] | None = None,
) -> tuple[str, int]:
    """Fetch state unemployment rates and write a cached parquet plus manifest.

    Raises :class:`AdapterError` on failure. Callers treat this source as optional and
    catch, so a BLS outage or rate limit degrades the panel rather than failing the run.
    """
    years = years or cfg.panel.permits_years
    states = states or (cfg.panel.states or sorted(STATE_FIPS))
    lo, hi = min(years), max(years)
    if hi - lo + 1 > MAX_YEARS_PER_QUERY:
        raise AdapterError(
            f"requested {hi - lo + 1} years, above the BLS unregistered limit of "
            f"{MAX_YEARS_PER_QUERY}"
        )

    target = cache_path(cfg, "bls_laus", FILENAME)
    if target.exists():
        return _coverage_of(pl.read_parquet(target))
    if cfg.offline:
        raise OfflineError(f"offline=True and LAUS not cached at {target}")

    sids = [series_id(s) for s in states if s.upper() in STATE_FIPS]
    rows: list[dict[str, Any]] = []
    raw_payloads: list[dict[str, Any]] = []

    for i in range(0, len(sids), MAX_SERIES_PER_QUERY):
        chunk = sids[i : i + MAX_SERIES_PER_QUERY]
        body = {"seriesid": chunk, "startyear": str(lo), "endyear": str(hi)}
        try:
            resp = requests.post(
                API,
                data=json.dumps(body),
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                timeout=90,
            )
        except requests.RequestException as exc:
            raise AdapterError(f"BLS LAUS request failed: {type(exc).__name__}: {exc}") from exc
        if resp.status_code != 200:
            raise AdapterError(f"BLS LAUS HTTP {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        if payload.get("status") != "REQUEST_SUCCEEDED":
            raise AdapterError(
                f"BLS LAUS returned status {payload.get('status')}: {payload.get('message')}"
            )
        raw_payloads.append({"seriesid_count": len(chunk), "message": payload.get("message")})

        for series in payload.get("Results", {}).get("series", []):
            st = _state_from_series(series.get("seriesID", ""))
            if st is None:
                continue
            for obs in series.get("data", []):
                period = obs.get("period", "")
                if not period.startswith("M") or period == "M13":  # M13 = annual average
                    continue
                try:
                    value = float(obs["value"])
                except (KeyError, ValueError):
                    continue
                rows.append(
                    {
                        "geography": st,
                        "year": int(obs["year"]),
                        "month": int(period[1:]),
                        "unemployment_rate": value,
                        "revised": any(
                            f.get("code") == "R" for f in (obs.get("footnotes") or []) if f
                        ),
                    }
                )

    if not rows:
        raise AdapterError("BLS LAUS returned no monthly observations")

    df = (
        pl.DataFrame(rows)
        .with_columns(pl.date(pl.col("year"), pl.col("month"), 1).alias("period"))
        .sort(["geography", "period"])
    )
    df.write_parquet(target)

    coverage, n = _coverage_of(df)
    write_manifest(
        target,
        name=SPEC.name,
        source=SPEC.source,
        source_url=API,
        license_terms=SPEC.license_terms,
        redistribution_status=SPEC.redistribution_status,
        schema_version=SPEC.schema_version,
        row_count=n,
        geographic_level="state",
        coverage_period=coverage,
        known_limitations=list(SPEC.known_limitations),
        data_class="PUBLIC",
        extra={
            "series_pattern": "LASST{fips}0000000000003 (state, seasonally adjusted, "
            "measure 03 = unemployment rate)",
            "n_states": df["geography"].n_unique(),
            "n_revised_observations": int(df["revised"].sum()),
            "api_queries": len(raw_payloads),
            "bulk_file_route_note": "download.bls.gov returns HTTP 403 to a generic "
            "client; BLS asks for a contact email in the User-Agent. We use the public "
            "API instead rather than send a personal address.",
        },
    )
    return (coverage, n)


def _coverage_of(df: pl.DataFrame) -> tuple[str, int]:
    return (f"{df['period'].min()}..{df['period'].max()}", df.height)


def load(cfg: Config) -> pl.DataFrame:
    """Load the cached LAUS panel. Raises ``FileNotFoundError`` when absent."""
    p = cache_path(cfg, "bls_laus", FILENAME)
    if not p.exists():
        raise FileNotFoundError(
            f"BLS LAUS not cached at {p}. It is OPTIONAL -- run `make fetch-public-data` "
            "to add it, or proceed without the unemployment control."
        )
    return pl.read_parquet(p)


def try_load(cfg: Config) -> pl.DataFrame | None:
    """Load if available, else ``None``. The pipeline uses this form."""
    try:
        return load(cfg)
    except (FileNotFoundError, Exception):
        return None
