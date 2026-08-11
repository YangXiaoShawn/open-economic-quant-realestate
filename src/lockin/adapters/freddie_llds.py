"""Freddie Mac Single-Family Loan-Level Dataset adapter (registered data only).

This adapter **never** downloads anything. The dataset is behind a registration
and licence-acceptance wall, and this repository does not bypass such walls. The
adapter's job is to locate files that the user placed in ``data/raw/freddie/``
themselves after accepting Freddie Mac's terms, and to hand a stream of chunks to
the parsers.

Supported layouts under ``data/raw/freddie/``:

* Quarterly full cohorts::

      historical_data_2021Q4.zip
        -> historical_data_2021Q4.txt        (origination, 32 fields)
           historical_data_time_2021Q4.txt   (performance, 32 fields)

* Official sample files::

      sample_2021.zip
        -> sample_orig_2021.txt
           sample_svcg_2021.txt

* Already-extracted ``.txt`` files with either naming convention.

Archives are read **without full extraction** via ``zipfile.ZipFile.open``, so a
multi-GB cohort never lands on disk twice.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

from lockin.config import Config

FileKind = Literal["origination", "performance"]

SPEC_NAME = "freddie_llds"
SOURCE = "Freddie Mac Single-Family Loan-Level Dataset"
SOURCE_URL = "https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset"
LICENSE_TERMS = (
    "Requires registration and acceptance of Freddie Mac's terms of use. "
    "Redistribution of the loan-level records is PROHIBITED. This repository "
    "reads only from a local path the user populated themselves and never "
    "commits, uploads, or republishes any loan-level record."
)
REDISTRIBUTION_STATUS = "RESTRICTED -- never redistributed"

KNOWN_LIMITATIONS = (
    "A SELECTED mortgage population: conventional, conforming, single-family, "
    "acquired by Freddie Mac. Excludes FHA/VA, jumbo, non-QM, bank-portfolio "
    "loans, and all-cash purchases. Contains no mortgage-free owners.",
    "Performance records begin at Freddie Mac ACQUISITION, not origination -- "
    "loans are LEFT TRUNCATED at their first observed loan age.",
    "Zero Balance Code 01 conflates voluntary payoff with scheduled maturity and "
    "does NOT distinguish refinance from sale-related payoff. No home-sale or "
    "household-move event can be constructed.",
    "Zero Balance Codes 15/16/96 are Freddie Mac portfolio and "
    "representation-and-warranty actions, not borrower decisions; treated as "
    "censoring.",
    "Postal code is truncated to the first three ZIP digits plus '00'. No property "
    "identifier exists, so loans cannot be linked across refinances (except within "
    "Relief Refinance chains via field 27).",
    "MSA/Metropolitan Division codes are NOT updated for changing OMB "
    "delineations; a versioned crosswalk is required for MSA analysis.",
    "Loan age RESETS on modification. Seller/servicer names below 1% of quarterly "
    "original UPB are collapsed to 'Other'.",
    "Freddie Mac's accounting cycle changed in 2019-05 from 16th-to-15th to "
    "calendar month; loan-month timestamps are not perfectly comparable across it.",
    "DTI is reported as Not Available above 65%, and for all HARP loans.",
)

_ORIG_PATTERNS = (
    re.compile(r"^historical_data_(\d{4}Q\d)\.txt$", re.I),
    re.compile(r"^sample_orig_(\d{4})\.txt$", re.I),
)
_PERF_PATTERNS = (
    re.compile(r"^historical_data_time_(\d{4}Q\d)\.txt$", re.I),
    re.compile(r"^sample_svcg_(\d{4})\.txt$", re.I),
)


@dataclass(frozen=True, slots=True)
class LoanFile:
    """A locatable origination or performance file, possibly inside a zip."""

    kind: FileKind
    cohort: str
    archive: Path | None
    member: str | None
    path: Path | None

    def describe(self) -> str:
        if self.archive is not None:
            return f"{self.archive.name}::{self.member}"
        return str(self.path)

    def open_text(self) -> IO[str]:
        """Open the file as a text stream without extracting the whole archive."""
        if self.archive is not None:
            zf = zipfile.ZipFile(self.archive)
            raw = zf.open(self.member or "", "r")
            import io

            return io.TextIOWrapper(raw, encoding="latin-1", errors="replace")
        if self.path is None:
            raise ValueError("LoanFile has neither archive nor path")
        return self.path.open("r", encoding="latin-1", errors="replace")


def _match(name: str, patterns: tuple[re.Pattern[str], ...]) -> str | None:
    base = Path(name).name
    for pat in patterns:
        m = pat.match(base)
        if m:
            return m.group(1)
    return None


def discover(cfg: Config, root: Path | None = None) -> list[LoanFile]:
    """Find every origination/performance file available under the raw directory.

    Returns an empty list -- rather than raising -- when the user has not yet
    completed registration, so callers can fall back to synthetic fixtures with a
    clear message.
    """
    base = root or cfg.path("raw", "freddie")
    if not base.exists():
        return []

    found: list[LoanFile] = []
    for p in sorted(base.rglob("*")):
        if p.is_dir():
            continue
        if p.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(p) as zf:
                    for member in zf.namelist():
                        if (c := _match(member, _ORIG_PATTERNS)) is not None:
                            found.append(LoanFile("origination", c, p, member, None))
                        elif (c := _match(member, _PERF_PATTERNS)) is not None:
                            found.append(LoanFile("performance", c, p, member, None))
            except zipfile.BadZipFile:
                continue
        elif p.suffix.lower() == ".txt":
            if (c := _match(p.name, _ORIG_PATTERNS)) is not None:
                found.append(LoanFile("origination", c, None, None, p))
            elif (c := _match(p.name, _PERF_PATTERNS)) is not None:
                found.append(LoanFile("performance", c, None, None, p))
    return found


def files_for(cfg: Config, kind: FileKind, cohorts: list[str] | None = None) -> list[LoanFile]:
    """Available files of one kind, filtered to the requested cohorts."""
    want = set(cohorts or cfg.mortgage.cohorts)
    return [f for f in discover(cfg) if f.kind == kind and (not want or f.cohort in want)]


def iter_lines(lf: LoanFile, chunk_rows: int) -> Iterator[list[str]]:
    """Yield lists of raw lines of at most ``chunk_rows`` each.

    Streaming by line keeps peak memory at O(chunk_rows) regardless of file size,
    which is what makes a multi-GB cohort tractable in ~16 GB of RAM.
    """
    buf: list[str] = []
    with lf.open_text() as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if not line:
                continue
            buf.append(line)
            if len(buf) >= chunk_rows:
                yield buf
                buf = []
    if buf:
        yield buf


def availability_message(cfg: Config) -> str:
    """Human-readable statement of what registered data is present, if any."""
    files = discover(cfg)
    if not files:
        return (
            "No registered Freddie Mac loan-level files found under "
            f"{cfg.path('raw', 'freddie')}. The pipeline will run on labeled "
            "SYNTHETIC fixtures. To use real data, follow data/DATA_ACCESS.md §R1 "
            "(registration and licence acceptance are yours to complete; this "
            "repository will not bypass them)."
        )
    orig = sorted({f.cohort for f in files if f.kind == "origination"})
    perf = sorted({f.cohort for f in files if f.kind == "performance"})
    return (
        f"Found registered Freddie Mac files: origination cohorts {orig}; "
        f"performance cohorts {perf}."
    )
