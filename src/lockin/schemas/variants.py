"""Layout variants of the Freddie Mac Standard dataset.

**Why this module exists.** As of the ``full_set_standard_historical_data.zip`` download
verified on 2026-08-13, the actual data files do **not** match the layout that Freddie
Mac publishes. Both official documents -- ``file_layout.xlsx`` (Last-Modified
2024-04-08) and ``user_guide.pdf`` -- describe 32 origination and 32 monthly performance
fields. The shipped files carry **31 and 35**.

This is exactly the situation ``AGENTS.md`` §3 is about: the documentation is not
authoritative here, and guessing a mapping from field names would be the wrong response.
What follows is the mapping actually **verified against the data**, with every inference
labelled by how strongly it is supported.

How the variant was established
-------------------------------

*Anchors.* Origination fields 1-24 and performance fields 1-32 are confirmed aligned
with the documented layout by a cross-file join on 2021Q4: for every one of 1,218
loan-months at loan age 0, origination field 13 (Original Interest Rate) equals
performance field 11 (Current Interest Rate), with zero mismatches. Performance field 26
(ELTV) likewise reproduces origination field 9 (CLTV) at origination.

*The two moved fields.* Value domains in 2021Q4 identify them:

* Origination field 25 (``Servicer Name``) is **absent** from the origination file, and
  performance field 34 holds servicer names ("NATIONSTAR MORTGAGE LLC DBA MR. COOPER",
  "ROCKET MORTGAGE, LLC"). Servicer Name **moved to the performance file**.
* Origination field 32 (``MI Cancellation Indicator``, documented domain Y/N/7/9) is
  absent, and performance field 33 has the observed domain ``{7, N, Y}`` -- the
  documented domain exactly. MI Cancellation **moved to the performance file**, which is
  coherent: it is a status that changes over the life of a loan, so a monthly file is
  where it belongs.

*The arithmetic closes on both files*, which is the check that makes the account
credible rather than merely possible:

===============  ==========================================================  =====
file             change                                                      count
===============  ==========================================================  =====
origination      32 − Servicer Name − MI Cancellation + 1 undocumented          31
performance      32 + MI Cancellation + Servicer Name + 1 undocumented          35
===============  ==========================================================  =====

*What remains genuinely unknown.* Origination field 31 is the constant ``9999`` across
all 40,000 probed rows, and performance field 35 is blank or ``0.00``. **Neither appears
in any official document.** They are parsed and carried through as
``undocumented_position_N`` and are never interpreted, never renamed to something
suggestive, and never used in any estimate.

**Nothing this project estimates depends on an inferred or undocumented field.** Every
research variable -- note rate, UPB, state, MSA, loan term, purpose, reporting period,
delinquency status, loan age, Zero Balance Code and its effective date, current rate --
sits inside the anchored, documented range. The inferred fields are available for
description only, and are marked ``support="inferred"`` so a consumer must opt in.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final, Literal

from lockin.schemas.freddie import (
    ORIGINATION_FIELDS,
    PERFORMANCE_FIELDS,
    Field,
)

Support = Literal["documented", "inferred", "undocumented"]

#: Fields the published documentation places in the origination file but which the
#: shipped files carry in the monthly performance file instead.
_MOVED_TO_PERFORMANCE: Final[tuple[str, ...]] = (
    "servicer_name",
    "mi_cancellation_indicator",
)


@dataclass(frozen=True, slots=True)
class LayoutVariant:
    """One observed on-disk layout of the Standard dataset."""

    key: str
    origination: tuple[Field, ...]
    performance: tuple[Field, ...]
    verified_against: str
    #: Field names whose position is inferred from value domains rather than read from
    #: an official document. Safe to describe, not safe to build an estimate on without
    #: saying so.
    inferred_fields: tuple[str, ...] = ()
    #: Positions present in the file that no official document describes at all.
    undocumented_positions: tuple[int, ...] = ()

    @property
    def n_origination(self) -> int:
        return len(self.origination)

    @property
    def n_performance(self) -> int:
        return len(self.performance)

    @property
    def origination_columns(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.origination)

    @property
    def performance_columns(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.performance)


def _renumber(fields: tuple[Field, ...]) -> tuple[Field, ...]:
    """Reassign 1-indexed positions after an insertion or removal."""
    return tuple(replace(f, position=i) for i, f in enumerate(fields, start=1))


def _undocumented(position: int, where: str) -> Field:
    return Field(
        position,
        f"undocumented_position_{position}",
        f"UNDOCUMENTED (position {position} of the {where} file)",
        "str",
        (),
        "Present in the shipped file; absent from file_layout.xlsx and user_guide.pdf. "
        "Parsed verbatim and never interpreted. Do not rename this without an official "
        "source -- a plausible name would be read as a documented meaning.",
    )


# --- v1: exactly what the published documentation describes -------------------

DOCUMENTED_32_32: Final[LayoutVariant] = LayoutVariant(
    key="documented_32_32",
    origination=ORIGINATION_FIELDS,
    performance=PERFORMANCE_FIELDS,
    verified_against=(
        "file_layout.xlsx and user_guide.pdf as published (layout Last-Modified "
        "2024-04-08). Matches the official sample files and archives issued under that "
        "layout."
    ),
)


# --- v2: what the 2026 full-set download actually contains ---------------------


def _build_31_35() -> LayoutVariant:
    kept = tuple(f for f in ORIGINATION_FIELDS if f.name not in _MOVED_TO_PERFORMANCE)
    orig = _renumber(kept) + (_undocumented(len(kept) + 1, "origination"),)

    by_name = {f.name: f for f in ORIGINATION_FIELDS}
    perf = (
        PERFORMANCE_FIELDS
        + (
            replace(
                by_name["mi_cancellation_indicator"],
                position=len(PERFORMANCE_FIELDS) + 1,
                note=(
                    "MOVED here from origination field 32. Position INFERRED from the "
                    "observed value domain {7, N, Y}, which is the documented domain "
                    "exactly; no official document places it here."
                ),
            ),
            replace(
                by_name["servicer_name"],
                position=len(PERFORMANCE_FIELDS) + 2,
                note=(
                    "MOVED here from origination field 25. Position INFERRED from "
                    "observed servicer names; no official document places it here. "
                    "Servicers below 1% of quarterly Original UPB are still collapsed "
                    "to 'Other servicers'."
                ),
            ),
        )
        + (_undocumented(len(PERFORMANCE_FIELDS) + 3, "performance"),)
    )
    return LayoutVariant(
        key="observed_31_35",
        origination=orig,
        performance=perf,
        verified_against=(
            "full_set_standard_historical_data.zip, probed 2026-08-13. Anchored by a "
            "cross-file join on 2021Q4: origination Original Interest Rate equals "
            "performance Current Interest Rate at loan age 0 for 1,218 of 1,218 records. "
            "Two moved fields identified by value domain. NOT described by any published "
            "Freddie Mac document."
        ),
        inferred_fields=_MOVED_TO_PERFORMANCE,
        undocumented_positions=(len(kept) + 1, len(PERFORMANCE_FIELDS) + 3),
    )


OBSERVED_31_35: Final[LayoutVariant] = _build_31_35()

VARIANTS: Final[dict[str, LayoutVariant]] = {v.key: v for v in (DOCUMENTED_32_32, OBSERVED_31_35)}

#: (n_origination_fields, n_performance_fields) -> variant key.
_BY_SHAPE: Final[dict[tuple[int, int], str]] = {
    (DOCUMENTED_32_32.n_origination, DOCUMENTED_32_32.n_performance): DOCUMENTED_32_32.key,
    (OBSERVED_31_35.n_origination, OBSERVED_31_35.n_performance): OBSERVED_31_35.key,
}


class UnknownLayoutError(RuntimeError):
    """The file's field counts match no variant this project has verified.

    Raised rather than guessed. A layout we have not checked against the data could
    silently shift the Zero Balance Code or the note rate by one position, which would
    corrupt every downstream estimate while parsing without error.
    """


def detect(n_origination: int, n_performance: int) -> LayoutVariant:
    """Pick the layout variant from the observed field counts.

    Field counts are the only signal available before parsing, and they happen to
    separate the two known variants cleanly.
    """
    key = _BY_SHAPE.get((n_origination, n_performance))
    if key is None:
        raise UnknownLayoutError(
            f"origination has {n_origination} fields and performance has "
            f"{n_performance}; known variants are "
            f"{sorted(_BY_SHAPE)}. Freddie Mac has changed the layout again. Re-verify "
            "against the data with a cross-file anchor before ingesting -- do NOT map "
            "positions from field names."
        )
    return VARIANTS[key]


def variant_for_origination(n_fields: int) -> LayoutVariant:
    """Variant lookup when only the origination file is in hand."""
    for v in VARIANTS.values():
        if v.n_origination == n_fields:
            return v
    raise UnknownLayoutError(
        f"no known variant has {n_fields} origination fields; known: "
        f"{sorted(v.n_origination for v in VARIANTS.values())}"
    )


def variant_for_performance(n_fields: int) -> LayoutVariant:
    """Variant lookup when only the performance file is in hand."""
    for v in VARIANTS.values():
        if v.n_performance == n_fields:
            return v
    raise UnknownLayoutError(
        f"no known variant has {n_fields} performance fields; known: "
        f"{sorted(v.n_performance for v in VARIANTS.values())}"
    )
