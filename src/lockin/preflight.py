"""Preflight checks for registered Freddie Mac loan-level data.

The full-data ingest is a multi-hour job. Discovering *after* it that the archives
were the wrong product, or that Freddie Mac changed the layout, is an expensive way to
find out. This module reads only the **first few hundred lines** of each file and
answers, in seconds:

* Are the archives the ones this pipeline expects, and which cohorts do they cover?
* Does each member parse at the verified field count?
* Are the Zero Balance Codes present all documented ones, or has Freddie Mac added a
  code whose meaning we would otherwise silently guess at?
* Do origination and performance files share loan sequence numbers, so the join will
  actually join?
* Roughly how much disk and time will the real run need?

Nothing here writes to ``data/interim`` or ``data/processed``.
"""

from __future__ import annotations

import contextlib
import zipfile
from dataclasses import dataclass, field
from typing import Any

from lockin.adapters import freddie_llds as llds
from lockin.config import Config
from lockin.schemas import variants
from lockin.schemas.freddie import (
    ORIGINATION_COLUMNS,
    PERFORMANCE_COLUMNS,
    SCHEMA_VERSION,
    ZERO_BALANCE_CODES,
)

#: How many lines to read from each file. Enough to catch a layout change and a
#: surprising code without touching the bulk of a multi-GB member.
PROBE_LINES = 500


@dataclass(slots=True)
class Finding:
    level: str  # BLOCKER | WARNING | INFO
    message: str


@dataclass(slots=True)
class Preflight:
    findings: list[Finding] = field(default_factory=list)
    cohorts_origination: list[str] = field(default_factory=list)
    cohorts_performance: list[str] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    zb_codes_seen: dict[str, int] = field(default_factory=dict)
    estimated: dict[str, Any] = field(default_factory=dict)

    def add(self, level: str, message: str) -> None:
        self.findings.append(Finding(level, message))

    @property
    def n_blockers(self) -> int:
        return sum(1 for f in self.findings if f.level == "BLOCKER")

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [{"level": f.level, "message": f.message} for f in self.findings],
            "cohorts_origination": self.cohorts_origination,
            "cohorts_performance": self.cohorts_performance,
            "files": self.files,
            "zero_balance_codes_seen": self.zb_codes_seen,
            "estimated": self.estimated,
            "schema_version": SCHEMA_VERSION,
            "n_blockers": self.n_blockers,
        }


def _probe(lf: llds.LoanFile, expected_fields: int, limit: int = PROBE_LINES) -> dict[str, Any]:
    """Read the head of one member and report what the layout looks like."""
    counts: dict[int, int] = {}
    seqs: set[str] = set()
    zb: dict[str, int] = {}
    n = 0
    zb_pos = PERFORMANCE_COLUMNS.index("zero_balance_code")
    try:
        with lf.open_text() as fh:
            for line in fh:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                parts = line.split("|")
                counts[len(parts)] = counts.get(len(parts), 0) + 1
                if parts:
                    seqs.add(
                        parts[0]
                        if lf.kind == "performance"
                        else parts[19]
                        if len(parts) > 19
                        else ""
                    )
                if lf.kind == "performance" and len(parts) > zb_pos:
                    code = parts[zb_pos].strip()
                    if code:
                        zb[code] = zb.get(code, 0) + 1
                n += 1
                if n >= limit:
                    break
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "lines_probed": n,
        "field_counts": counts,
        "modal_field_count": max(counts, key=lambda k: counts[k]) if counts else 0,
        "expected_field_count": expected_fields,
        "sample_loan_seq_nos": sorted(x for x in seqs if x)[:3],
        "zero_balance_codes": zb,
    }


def run_preflight(cfg: Config) -> Preflight:
    """Inspect whatever the user has placed under the raw data directory.

    Searches the same locations as :func:`lockin.adapters.freddie_llds.discover` --
    ``data/raw/freddie`` and ``data/raw`` itself -- because people put the download where
    the browser left it. Preflight checking a narrower path than the ingester would tell
    a user their data was missing while the ingester happily read it, or the reverse.
    """
    pf = Preflight()
    preferred = cfg.path("raw", "freddie")
    raw_root = cfg.path("raw")

    if not preferred.exists() and not raw_root.exists():
        pf.add(
            "BLOCKER",
            f"Neither {preferred} nor {raw_root} exists. Create one and place the "
            "archives you downloaded after completing registration. See "
            "data/DATA_ACCESS.md section R1.",
        )
        return pf

    files = llds.discover(cfg)
    if not files:
        looked = [d for d in (preferred, raw_root) if d.exists()]
        contents = sorted({p.name for d in looked for p in d.iterdir()})[:10]
        pf.add(
            "BLOCKER",
            f"No recognised Freddie Mac files under {[str(d) for d in looked]}. Found: "
            f"{contents or 'nothing'}. Expected historical_data_YYYYQn.zip, "
            "sample_YYYY.zip, or the combined full-set archive, unmodified.",
        )
        return pf

    # Only the cohorts this run will actually ingest are worth probing: the full set has
    # 110 cohorts and probing all of them to report on 40 wastes the point of preflight.
    wanted = set(cfg.mortgage.cohorts)
    if wanted:
        selected = [f for f in files if f.cohort in wanted]
        if selected:
            skipped = len(files) - len(selected)
            files = selected
            if skipped:
                pf.add(
                    "INFO",
                    f"{skipped} member file(s) present but outside mortgage.cohorts and "
                    "not probed. Cohort choice is a POPULATION RESTRICTION -- see the "
                    "header of the run profile for its direction of bias.",
                )

    pf.cohorts_origination = sorted({f.cohort for f in files if f.kind == "origination"})
    pf.cohorts_performance = sorted({f.cohort for f in files if f.kind == "performance"})
    pf.add(
        "INFO",
        f"Found {len(files)} member file(s): origination cohorts "
        f"{pf.cohorts_origination or 'NONE'}; performance cohorts "
        f"{pf.cohorts_performance or 'NONE'}.",
    )

    # -- pairing -------------------------------------------------------------
    only_orig = set(pf.cohorts_origination) - set(pf.cohorts_performance)
    only_perf = set(pf.cohorts_performance) - set(pf.cohorts_origination)
    if only_orig:
        pf.add(
            "BLOCKER",
            f"cohorts with origination but NO performance file: {sorted(only_orig)}. "
            "The loan-event table cannot be built for them.",
        )
    if only_perf:
        pf.add(
            "WARNING",
            f"cohorts with performance but no origination file: {sorted(only_perf)}. "
            "Their loan-months will be dropped by the inner join.",
        )

    configured = set(cfg.mortgage.cohorts)
    usable = set(pf.cohorts_origination) & set(pf.cohorts_performance)
    missing = configured - usable
    if missing:
        pf.add(
            "WARNING",
            f"config requests cohorts {sorted(configured)} but only {sorted(usable)} are "
            f"present. Missing: {sorted(missing)}. Either download them or narrow "
            "mortgage.cohorts.",
        )

    # -- per-file probe ------------------------------------------------------
    total_bytes = 0
    variants_seen: dict[str, int] = {}
    for lf in files:
        expected = (
            len(ORIGINATION_COLUMNS) if lf.kind == "origination" else len(PERFORMANCE_COLUMNS)
        )
        res = _probe(lf, expected)
        # Report the count the DETECTED variant expects, not the documented one, or the
        # table reads as 80 mismatches on a clean run.
        try:
            _v = (
                variants.variant_for_origination(res.get("modal_field_count", 0))
                if lf.kind == "origination"
                else variants.variant_for_performance(res.get("modal_field_count", 0))
            )
            res["expected_field_count"] = (
                _v.n_origination if lf.kind == "origination" else _v.n_performance
            )
        except variants.UnknownLayoutError:
            pass
        size = _member_size(lf)
        total_bytes += size
        pf.files.append(
            {
                "kind": lf.kind,
                "cohort": lf.cohort,
                "source": lf.describe(),
                "uncompressed_bytes": size,
                **res,
            }
        )
        if "error" in res:
            pf.add("BLOCKER", f"{lf.describe()} could not be read: {res['error']}")
            continue
        modal = res["modal_field_count"]
        # A field count is a BLOCKER only when it matches no layout variant this project
        # has verified against the data. Freddie Mac's published documentation is
        # currently behind the shipped files (32/32 documented, 31/35 shipped), so
        # insisting on the documented count alone would reject the real dataset.
        try:
            v = (
                variants.variant_for_origination(modal)
                if lf.kind == "origination"
                else variants.variant_for_performance(modal)
            )
            variants_seen[v.key] = variants_seen.get(v.key, 0) + 1
            pf.files[-1]["layout_variant"] = v.key
        except variants.UnknownLayoutError as exc:
            pf.add(
                "BLOCKER",
                f"{lf.describe()} has {modal} pipe-delimited fields, which matches no "
                f"verified layout variant. {exc} Do NOT ingest until the layout is "
                "re-verified against the data with a cross-file anchor.",
            )

        if len(res["field_counts"]) > 1:
            pf.add(
                "WARNING",
                f"{lf.describe()} has ragged rows: field counts {res['field_counts']}. "
                "The parser tolerates this, but check the source download completed.",
            )
        for code, k in res.get("zero_balance_codes", {}).items():
            pf.zb_codes_seen[code] = pf.zb_codes_seen.get(code, 0) + k

    if len(variants_seen) > 1:
        pf.add(
            "WARNING",
            f"more than one layout variant present: {variants_seen}. Each file is parsed "
            "with its own variant, and every research variable sits in the positions the "
            "variants share, so this is handled -- but mixing vintages in one run is "
            "worth knowing about.",
        )
    for key, n in variants_seen.items():
        v = variants.VARIANTS[key]
        if v.inferred_fields or v.undocumented_positions:
            pf.add(
                "INFO",
                f"{n} file(s) use layout {key}, which is NOT described by any published "
                f"Freddie Mac document. Fields {list(v.inferred_fields)} are placed by "
                f"INFERENCE from value domains and positions {list(v.undocumented_positions)} "
                "are undocumented and never interpreted. No research variable depends on "
                "either -- see lockin/schemas/variants.py.",
            )

    # -- undocumented ZB codes ----------------------------------------------
    unknown = {c: n for c, n in pf.zb_codes_seen.items() if c.zfill(2) not in ZERO_BALANCE_CODES}
    if unknown:
        pf.add(
            "BLOCKER",
            f"UNDOCUMENTED Zero Balance Code(s) in the probe: {unknown}. This pipeline "
            "refuses to guess an outcome from an unknown code -- it would censor them, "
            "silently discarding real exits. Check the current user guide and add them "
            "to lockin/schemas/freddie.py before ingesting.",
        )
    elif pf.zb_codes_seen:
        pf.add(
            "INFO",
            "Zero Balance Codes seen in the probe (all documented): "
            + ", ".join(
                f"{c}={n} ({ZERO_BALANCE_CODES[c.zfill(2)].official_label})"
                for c, n in sorted(pf.zb_codes_seen.items())
            ),
        )

    # -- join sanity ---------------------------------------------------------
    for cohort in sorted(usable):
        o = next(
            (f for f in pf.files if f["kind"] == "origination" and f["cohort"] == cohort), None
        )
        p = next(
            (f for f in pf.files if f["kind"] == "performance" and f["cohort"] == cohort), None
        )
        if not o or not p or "error" in o or "error" in p:
            continue
        o_pref = {s[:5] for s in o.get("sample_loan_seq_nos", [])}
        p_pref = {s[:5] for s in p.get("sample_loan_seq_nos", [])}
        if o_pref and p_pref and not (o_pref & p_pref):
            pf.add(
                "WARNING",
                f"cohort {cohort}: origination and performance loan-sequence prefixes "
                f"differ ({sorted(o_pref)} vs {sorted(p_pref)}). Probe reads only the "
                "head of each file, so this may be benign, but confirm the two files "
                "belong to the same cohort.",
            )

    # -- resource estimate ---------------------------------------------------
    pf.estimated = _estimate(pf, total_bytes, cfg)
    pf.add(
        "INFO",
        f"Estimated uncompressed input ~{total_bytes / 1e9:.1f} GB. "
        f"Peak memory stays at O(chunk_rows={cfg.mortgage.chunk_rows:,}) because the "
        "parsers stream; the binding constraint is disk and wall time, not RAM.",
    )
    if pf.estimated.get("episode_rows_estimate", 0) > cfg.survival.max_episode_rows:
        pf.add(
            "WARNING",
            f"estimated episode rows ~{pf.estimated['episode_rows_estimate']:,} exceeds "
            f"survival.max_episode_rows={cfg.survival.max_episode_rows:,}. The run will "
            "stop rather than swap. Either raise the budget or set "
            "survival.non_event_sample_fraction below 1.0 to enable case-cohort "
            "sampling (all exit months kept, non-exit months sampled with weights).",
        )

    if cfg.mortgage.mode == "synthetic":
        pf.add(
            "WARNING",
            "config mortgage.mode is still 'synthetic'. Set it to 'registered_sample' "
            "or 'registered_full' so artifacts stop being stamped SYNTHETIC and the "
            "report banners are dropped.",
        )
    return pf


def _member_size(lf: llds.LoanFile) -> int:
    """Uncompressed size of the member, descending through any nesting.

    Reads central directories only. Without the nesting walk this returned 0 for every
    file in the combined full-set archive, which made the resource estimate report
    "~0.0 GB" for a 40 GB input -- a reassuring number that was pure artifact.
    """
    try:
        if lf.archive is None:
            return lf.path.stat().st_size if lf.path else 0
        handles: list[Any] = []
        zf = zipfile.ZipFile(lf.archive)
        handles.append(zf)
        for inner in lf.nesting:
            fh = zf.open(inner, "r")
            handles.append(fh)
            zf = zipfile.ZipFile(fh)
            handles.append(zf)
        size = zf.getinfo(lf.member or "").file_size
        for h in reversed(handles):
            with contextlib.suppress(Exception):
                h.close()
        return size
    except Exception:
        return 0


def _estimate(pf: Preflight, total_bytes: int, cfg: Config) -> dict[str, Any]:
    """Rough sizing from the probe. Deliberately crude and labeled as such."""
    perf_files = [f for f in pf.files if f["kind"] == "performance" and "error" not in f]
    if not perf_files:
        return {"note": "no readable performance file to size from"}
    # Mean bytes per line from the probe, applied to the member size.
    est_rows = 0
    for f in perf_files:
        probed = f.get("lines_probed", 0)
        if probed and f.get("uncompressed_bytes"):
            # We do not know probe byte length exactly; assume ~200 bytes/row, the
            # typical width of a 32-field performance record.
            est_rows += int(f["uncompressed_bytes"] / 200)
    return {
        "method": "crude: uncompressed member size / ~200 bytes per performance record",
        "performance_rows_estimate": est_rows,
        "episode_rows_estimate": est_rows,
        "note": (
            "The episode table is filtered to "
            f"{cfg.mortgage.performance_start}..{cfg.mortgage.performance_end} at parse "
            "time, so the realised count is usually far below this upper bound."
        ),
        "parquet_size_hint_gb": round(total_bytes / 1e9 * 0.15, 2),
    }


def registration_steps() -> list[str]:
    """The steps only the user can perform, in order.

    Verified against the live pages on 2026-08-11. Note that many older tutorials still
    point at ``freddiemac.embs.com``; that host now returns HTTP 403 and is not the
    current route.
    """
    return [
        "1. Open the dataset page: "
        "https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset",
        "2. It states: 'To access the Single-Family Loan-Level Dataset, register and "
        "sign-in to CLARITY DATA INTELLIGENCE.' That portal is the actual gate. The "
        "download entry point is https://claritydownload.fmapps.freddiemac.com/CRT/ "
        "(SAML sign-in). NOTE: the old freddiemac.embs.com route that older guides "
        "cite now returns HTTP 403 -- do not follow those.",
        "3. Register for a Clarity account and ACCEPT THE TERMS OF USE. Read them: they "
        "prohibit redistributing the loan-level records. This repository will not "
        "bypass this step and does not want a copy of your credentials.",
        "4. In Clarity, download either the official SAMPLE files (sample_YYYY.zip -- a "
        "random 50,000-loan sample per origination year; the right choice for a first "
        "real run) or the full quarterly cohorts (historical_data_YYYYQn.zip). Both "
        "sit behind the same sign-in.",
        "5. Put the archives UNMODIFIED in data/raw/freddie/. Do not unzip them; the "
        "adapter reads members in place.",
        "6. Run `make check-registered-data` -- seconds, not hours. Fix any BLOCKER.",
        "7. Set mortgage.mode to registered_sample (or registered_full) in your config, "
        "and set mortgage.cohorts to the cohorts you actually downloaded.",
        "8. Run `make reproduce-sample`. No code changes are needed.",
    ]
