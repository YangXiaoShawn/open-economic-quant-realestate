"""Teleworkable employment share (Dingel & Neiman 2020) -- **optional**.

Supplies the predetermined share of jobs in a geography that *can* be performed at
home. This is the control that addresses the **remote-work exposure** threat in
``docs/IDENTIFICATION_STRATEGY.md`` §3.2, which is the last threat still recorded as
``UNCONTROLLED`` on every event-study artifact.

Why the threat is real: the 2020-2022 shift to remote work moved housing demand across
geographies (toward cheaper, lower-density markets) for reasons that have nothing to do
with mortgage lock-in. If teleworkable-heavy places also happen to have a distinctive
pre-shock coupon distribution -- and they plausibly do, because they are richer, more
urban, and refinanced more in 2020-21 -- then the exposure coefficient picks up the
remote-work reallocation instead of lock-in.

**How it must enter the regression, and why the obvious way is wrong.**

The Dingel-Neiman measure is a **single cross-section**: one number per geography,
constant over time. Adding it as an ordinary level control does nothing useful -- it is
exactly collinear with the geography fixed effects, and nothing raises: the
pseudo-inverse just splits the coefficient arbitrarily between the control and the fixed
effects. The artifact would then *claim* a control that constrains no estimate, which is
precisely the class of error ``DECISION_LOG`` D026 was opened for.
``lockin.panel.eventstudy`` therefore detects zero within-geography variance, moves the
column to the trend-control set, and records the move under ``degenerate_controls``.

It therefore enters as a **trend control**: interacted with every period indicator,
exactly parallel to how exposure itself enters. That allows places with more teleworkable
jobs to be on their own arbitrary time path, and asks whether the exposure coefficient
survives. See :func:`lockin.panel.eventstudy.event_study` and its ``trend_controls``
argument.

Source and vintage:

* Dingel, Jonathan I., and Brent Neiman (2020), "How many jobs can be done at home?",
  *Journal of Public Economics* 189: 104235. Replication code and outputs are public on
  GitHub under the authors' repository; no registration.
* The occupation-level teleworkability classification is built from O*NET "Work Context"
  and "Generalized Work Activities" survey modules. The geographic aggregation weights
  those occupations by **BLS OES employment (2018)** -- i.e. a *pre-pandemic* weighting.
  For this project that is a feature, not a limitation: the control is predetermined
  relative to both the 2020-21 refinance boom and the 2022 rate shock.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Final

import polars as pl

from lockin.adapters.base import AdapterError, SourceSpec, cache_path, download
from lockin.config import Config
from lockin.manifest import write_manifest

_REPO: Final[str] = "https://raw.githubusercontent.com/jdingel/DingelNeiman-workathome/master"

STATE_URL: Final[str] = f"{_REPO}/state_measures/output/state_workfromhome.csv"
MSA_URL: Final[str] = f"{_REPO}/MSA_measures/output/MSA_workfromhome.csv"

#: The four measures the authors publish, along two axes.
#:
#: *Classification.* The **unprefixed** pair is the paper's baseline: occupations are
#: classified from the O*NET "Work Context" and "Generalized Work Activities" survey
#: responses by a fixed, documented rule. The ``manual`` pair instead uses the authors'
#: own subjective judgement of each occupation (their replication package calls the
#: input file ``Teleworkable_BNJDopinion.csv``) and is presented as a robustness check,
#: not the headline.
#:
#: *Weighting.* ``_emp`` weights occupations by employment, ``_wage`` by the wage bill.
#:
#: The default is therefore ``teleworkable_emp``: the survey-based rule rather than
#: expert opinion, because a control variable built from a reproducible classification
#: is auditable by a reader who distrusts ours, and employment weighting because the
#: quantity that moves housing demand is how many people can work from home, not how
#: much they are paid. The other three are kept so the choice can be varied in the
#: robustness grid rather than asserted.
MEASURES: Final[tuple[str, ...]] = (
    "teleworkable_emp",
    "teleworkable_wage",
    "teleworkable_manual_emp",
    "teleworkable_manual_wage",
)
DEFAULT_MEASURE: Final[str] = "teleworkable_emp"

SPEC = SourceSpec(
    name="teleworkable",
    source=(
        "Dingel, Jonathan I. and Brent Neiman (2020), 'How many jobs can be done at "
        "home?', Journal of Public Economics 189:104235; authors' public replication "
        "outputs (O*NET work-context modules aggregated with BLS OES 2018 employment "
        "weights)."
    ),
    urls=(STATE_URL, MSA_URL),
    license_terms=(
        "Authors' replication materials, publicly posted. Cite Dingel & Neiman (2020). "
        "O*NET is published by the U.S. Department of Labor under CC BY 4.0; BLS OES is "
        "a U.S. Government work."
    ),
    redistribution_status="public; cached copy not committed",
    geographic_level="state and CBSA",
    known_limitations=(
        "FEASIBILITY, NOT BEHAVIOUR. The measure is the share of jobs that *could* be "
        "done at home, not the share actually done at home. Realised remote work "
        "differed enormously across places with identical feasibility.",
        "TIME-INVARIANT. One cross-section, so it cannot enter as a level control "
        "alongside geography fixed effects -- it must be interacted with time. The "
        "adapter does not enforce this; lockin.panel.eventstudy does.",
        "OCCUPATION-LEVEL AND BINARY. Every job in an occupation is classified the same "
        "way, so within-occupation heterogeneity (a teleworkable role at a "
        "non-teleworkable employer, and the reverse) is assumed away.",
        "BUILT FROM PRE-PANDEMIC O*NET SURVEYS. Occupations whose technology changed "
        "during 2020-2022 are classified as they were before.",
        "EMPLOYMENT WEIGHTS ARE 2018 OES. Places whose industry mix shifted after 2018 "
        "are mismeasured. This is deliberate -- a predetermined weight is what makes the "
        "control usable -- but it is still measurement error.",
        "MSA FILE IS KEYED BY CBSA CODE UNDER THE DELINEATION IN FORCE AT PUBLICATION "
        "(2020 vintage, i.e. the 2018 OMB delineation). Joining it to Freddie Mac MSA "
        "codes requires the versioned crosswalk in lockin.adapters.omb_cbsa; the codes "
        "are NOT interchangeable across vintages.",
        "Puerto Rico CBSAs are present in the MSA file but have no Freddie Mac or FHFA "
        "counterpart in this project and are dropped downstream.",
        "THE STATE-LEVEL FILE IS A CONTRIBUTED AGGREGATION, credited in the authors' "
        "repository to Ole Agersnap, not a table from the published paper. It is used "
        "here because the panel's default geography is the state, but it has not been "
        "through the paper's own review.",
    ),
    schema_version="dingel-neiman-2020-v1",
)

STATE_FILENAME = "teleworkable_state.parquet"
MSA_FILENAME = "teleworkable_msa.parquet"

#: state FIPS -> USPS abbreviation. The published file keys on FIPS and carries the full
#: state name; the rest of this project keys on the two-letter abbreviation that Freddie
#: Mac, FHFA, and the Census BPS all use.
_FIPS_TO_USPS: Final[dict[int, str]] = {
    1: "AL",
    2: "AK",
    4: "AZ",
    5: "AR",
    6: "CA",
    8: "CO",
    9: "CT",
    10: "DE",
    11: "DC",
    12: "FL",
    13: "GA",
    15: "HI",
    16: "ID",
    17: "IL",
    18: "IN",
    19: "IA",
    20: "KS",
    21: "KY",
    22: "LA",
    23: "ME",
    24: "MD",
    25: "MA",
    26: "MI",
    27: "MN",
    28: "MS",
    29: "MO",
    30: "MT",
    31: "NE",
    32: "NV",
    33: "NH",
    34: "NJ",
    35: "NM",
    36: "NY",
    37: "NC",
    38: "ND",
    39: "OH",
    40: "OK",
    41: "OR",
    42: "PA",
    44: "RI",
    45: "SC",
    46: "SD",
    47: "TN",
    48: "TX",
    49: "UT",
    50: "VT",
    51: "VA",
    53: "WA",
    54: "WV",
    55: "WI",
    56: "WY",
    72: "PR",
}


def _parse_csv(body: bytes, key_field: str) -> list[dict[str, Any]]:
    """Parse a published work-from-home CSV into typed rows.

    The published files write proportions in Stata's bare-decimal form (``.26517713``
    with no leading zero). ``float()`` accepts that, but a naive string comparison or a
    Polars ``cast`` on a mixed column would not, so parsing is explicit here.
    """
    rows: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    missing = [m for m in MEASURES if m not in (reader.fieldnames or [])]
    if missing:
        raise AdapterError(
            f"teleworkable file is missing expected measure column(s) {missing}; "
            f"columns present: {reader.fieldnames}. The upstream layout changed -- "
            "re-verify before using, do not silently fall back."
        )
    if key_field not in (reader.fieldnames or []):
        raise AdapterError(f"teleworkable file has no {key_field!r} column")

    for raw in reader:
        rec: dict[str, Any] = {}
        try:
            rec["area_code"] = int(str(raw["AREA"]).strip())
        except (TypeError, ValueError):
            continue
        rec["area_name"] = (raw.get(key_field) or "").strip().strip('"')
        ok = True
        for m in MEASURES:
            v = (raw.get(m) or "").strip()
            try:
                rec[m] = float(v)
            except ValueError:
                ok = False
                break
        if ok:
            rows.append(rec)
    if not rows:
        raise AdapterError("teleworkable file parsed to zero usable rows")
    return rows


def fetch(cfg: Config) -> dict[str, int]:
    """Fetch both the state and CBSA teleworkable files. Returns row counts.

    Raises :class:`AdapterError` on failure. Callers treat this source as optional and
    catch, so an upstream outage degrades the identification-threat coverage rather than
    failing the run.
    """
    out: dict[str, int] = {}

    for level, url, filename, key in (
        ("state", STATE_URL, STATE_FILENAME, "STATE"),
        ("msa", MSA_URL, MSA_FILENAME, "AREA_NAME"),
    ):
        target = cache_path(cfg, "teleworkable", filename)
        raw_target = target.with_suffix(".csv")
        path, used = download(cfg, (url,), raw_target, max_age_days=365, min_bytes=512)
        rows = _parse_csv(path.read_bytes(), key)

        df = pl.DataFrame(rows)
        if level == "state":
            df = (
                df.with_columns(
                    pl.col("area_code")
                    .replace_strict(_FIPS_TO_USPS, default=None)
                    .alias("geography")
                )
                .drop_nulls("geography")
                .rename({"area_name": "state_name"})
            )
        else:
            df = df.with_columns(
                pl.col("area_code").cast(pl.Utf8).alias("geography"),
            ).rename({"area_name": "cbsa_title"})

        df = df.with_columns(pl.col(DEFAULT_MEASURE).alias("teleworkable_share")).sort("geography")
        df.write_parquet(target)

        write_manifest(
            target,
            name=f"{SPEC.name}_{level}",
            source=SPEC.source,
            source_url=used,
            license_terms=SPEC.license_terms,
            redistribution_status=SPEC.redistribution_status,
            schema_version=SPEC.schema_version,
            row_count=df.height,
            geographic_level=level,
            coverage_period="single cross-section; O*NET occupation scores x BLS OES 2018 weights",
            known_limitations=list(SPEC.known_limitations),
            data_class="PUBLIC",
            extra={
                "default_measure": DEFAULT_MEASURE,
                "measures_available": list(MEASURES),
                "mean_teleworkable_share": round(float(df["teleworkable_share"].mean()), 6),
                "sd_teleworkable_share": round(float(df["teleworkable_share"].std()), 6),
                "cbsa_delineation_vintage": (
                    "2018 OMB delineation (the vintage in force when the authors "
                    "published); join through lockin.adapters.omb_cbsa, never directly"
                )
                if level == "msa"
                else "n/a",
                "enters_regression_as": (
                    "trend control -- interacted with every period indicator, because "
                    "the measure is time-invariant and would otherwise be absorbed by "
                    "the geography fixed effects"
                ),
            },
        )
        out[level] = df.height

    return out


def load(cfg: Config, level: str | None = None) -> pl.DataFrame:
    """Load the cached teleworkable table for ``level`` (default: the panel geography)."""
    level = level or cfg.panel.geography
    filename = STATE_FILENAME if level == "state" else MSA_FILENAME
    p = cache_path(cfg, "teleworkable", filename)
    if not p.exists():
        raise FileNotFoundError(
            f"teleworkable ({level}) not cached at {p}. It is OPTIONAL -- run "
            "`make fetch-public-data` to add it, or proceed with the remote-work "
            "threat recorded as UNCONTROLLED."
        )
    return pl.read_parquet(p)


def try_load(cfg: Config, level: str | None = None) -> pl.DataFrame | None:
    """Load if available, else ``None``. The pipeline uses this form."""
    try:
        return load(cfg, level)
    except (FileNotFoundError, OSError):
        return None
