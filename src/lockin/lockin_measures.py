"""The eight mortgage lock-in measures.

Every measure is **point-in-time**: it is a function of the borrower's note rate
and remaining balance/term as of a date ``t``, and of the market mortgage rate
that a lender would have quoted *at or before* ``t``. No measure may use a market
rate observed after ``t``; ``lockin.rates.align_point_in_time`` enforces that and
``tests/test_rate_alignment.py`` tests it.

Sign conventions, fixed once and used everywhere:

* ``rate_gap``      = market rate - note rate.  **Positive => locked in.**
* ``lockin_gap``    = max(rate_gap, 0).          Positive part of the above.
* ``refi_incentive``= note rate - market rate = -rate_gap. **Positive => refi pays.**

A borrower cannot be simultaneously locked in and have a refinance incentive;
``lockin_gap`` and ``max(refi_incentive, 0)`` are mutually exclusive by construction.

Borrower-level measures (1-5) take arrays of loans. Geography-level measures
(6-8) aggregate them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

from lockin.amortization import annuity_factor, payment

FloatArray = npt.NDArray[np.float64]
Numeric = float | FloatArray

#: Default basis-point thresholds for "locked in" indicators.
DEFAULT_THRESHOLDS_BP: Final[tuple[int, ...]] = (100, 200, 300, 400)

#: Default expected remaining holding period, in months, for the PV financing gap.
#: 84 months (~7 years) is the conventional planning horizon used in mortgage
#: pricing; it is a **calibrated**, not estimated, input and is recorded as such.
DEFAULT_HOLDING_PERIOD_MONTHS: Final[int] = 84

#: Default annual discount rate (percent) for the PV financing gap.
DEFAULT_DISCOUNT_RATE_PCT: Final[float] = 4.0


# ---------------------------------------------------------------------------
# 1-3. Rate-based measures
# ---------------------------------------------------------------------------


def rate_gap(market_rate_pct: Numeric, note_rate_pct: Numeric) -> FloatArray:
    """Measure 1 -- raw rate gap: market rate minus the borrower's note rate."""
    return np.asarray(
        np.asarray(market_rate_pct, dtype=float) - np.asarray(note_rate_pct, dtype=float),
        dtype=float,
    )


def lockin_gap(market_rate_pct: Numeric, note_rate_pct: Numeric) -> FloatArray:
    """Measure 2 -- positive lock-in gap: ``max(market - note, 0)``."""
    return np.asarray(np.clip(rate_gap(market_rate_pct, note_rate_pct), 0.0, None), dtype=float)


def refi_incentive(market_rate_pct: Numeric, note_rate_pct: Numeric) -> FloatArray:
    """Measure 3 -- refinancing incentive: ``note - market``.

    Positive means the borrower could lower the coupon by refinancing. This is
    the *incentive*, not a prediction of refinancing: it ignores closing costs,
    credit eligibility, remaining term, and the option value of waiting.
    """
    return -rate_gap(market_rate_pct, note_rate_pct)


# ---------------------------------------------------------------------------
# 4-5. Dollar-denominated measures
# ---------------------------------------------------------------------------


def payment_gap(
    balance: Numeric,
    note_rate_pct: Numeric,
    market_rate_pct: Numeric,
    remaining_term_months: Numeric,
) -> FloatArray:
    r"""Measure 4 -- payment-equivalent lock-in cost, dollars per month.

    The change in scheduled principal-and-interest if the borrower replaced the
    *remaining balance* over the *remaining term* at the current market rate:

    .. math::
        \Delta_t = \mathrm{PMT}(B_t, r_t, n_t) - \mathrm{PMT}(B_t, r_0, n_t)

    Positive => moving/refinancing raises the payment => locked in.

    **Interpretation caveat.** Holding the remaining term fixed is a deliberate
    conservative choice. A borrower who moves typically takes a *fresh* 30-year
    term on a *different* (usually larger) balance, so this understates the
    payment shock for trade-up buyers and overstates it for borrowers late in
    their term who would re-extend. ``payment_gap_fresh_term`` gives the
    alternative.
    """
    b = np.asarray(balance, dtype=float)
    n = np.asarray(remaining_term_months, dtype=float)
    return np.asarray(payment(b, market_rate_pct, n) - payment(b, note_rate_pct, n), dtype=float)


def payment_gap_fresh_term(
    balance: Numeric,
    note_rate_pct: Numeric,
    market_rate_pct: Numeric,
    remaining_term_months: Numeric,
    fresh_term_months: int = 360,
) -> FloatArray:
    """Variant of measure 4 where the replacement loan takes a fresh full term.

    Compares the borrower's current scheduled payment on the remaining term
    against a new ``fresh_term_months`` loan at the market rate for the same
    balance. Exercised by the ``payment_gap_fresh_term`` cell in
    ``outputs/hazards/sensitivity_cells``; not the default.
    """
    b = np.asarray(balance, dtype=float)
    n = np.asarray(remaining_term_months, dtype=float)
    return np.asarray(
        payment(b, market_rate_pct, float(fresh_term_months)) - payment(b, note_rate_pct, n),
        dtype=float,
    )


def pv_financing_gap(
    balance: Numeric,
    note_rate_pct: Numeric,
    market_rate_pct: Numeric,
    remaining_term_months: Numeric,
    holding_period_months: int = DEFAULT_HOLDING_PERIOD_MONTHS,
    discount_rate_pct: float = DEFAULT_DISCOUNT_RATE_PCT,
) -> FloatArray:
    r"""Measure 5 -- present value of the additional financing cost, dollars.

    .. math::
        \mathrm{PVGap}_t = \Delta_t \cdot a_{H,\delta},
        \qquad a_{H,\delta} = \frac{1-(1+\delta)^{-H}}{\delta}

    The holding period :math:`H` is capped at the remaining term (you cannot pay
    the differential for longer than the loan exists).

    Both :math:`H` and :math:`\delta` are **calibrated** inputs, not estimated.
    Every artifact that uses this measure records their values.
    """
    d = payment_gap(balance, note_rate_pct, market_rate_pct, remaining_term_months)
    n = np.asarray(remaining_term_months, dtype=float)
    h = np.minimum(np.full_like(n, float(holding_period_months)), n)
    return np.asarray(d * annuity_factor(h, discount_rate_pct), dtype=float)


# ---------------------------------------------------------------------------
# 6-8. Geography-level exposure measures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExposureResult:
    """Geography-level lock-in exposure, both weighting schemes.

    ``locked_share_count`` / ``locked_share_upb`` are dicts keyed by the
    basis-point threshold.
    """

    n_loans: int
    total_upb: float
    wavg_note_rate_count: float
    wavg_note_rate_upb: float
    market_rate: float
    locked_share_count: dict[int, float]
    locked_share_upb: dict[int, float]
    mean_lockin_gap: float
    median_payment_gap: float
    mean_payment_gap: float
    refi_incentive_share: float
    """Share of loans whose refi incentive exceeds 50 bp -- reported alongside
    lock-in so the two states are never conflated."""


def locked_in_share(
    market_rate_pct: float,
    note_rate_pct: FloatArray,
    weights: FloatArray | None = None,
    thresholds_bp: tuple[int, ...] = DEFAULT_THRESHOLDS_BP,
) -> dict[int, float]:
    """Measures 6-8 core -- weighted share of loans locked in above each threshold.

    ``weights=None`` gives measure 8 (loan-count weights, every loan equal).
    ``weights=current_upb`` gives measure 7 (UPB-weighted exposure).
    """
    note = np.asarray(note_rate_pct, dtype=float)
    if note.size == 0:
        return {t: float("nan") for t in thresholds_bp}
    w = np.ones_like(note) if weights is None else np.asarray(weights, dtype=float)
    valid = np.isfinite(note) & np.isfinite(w) & (w >= 0)
    note, w = note[valid], w[valid]
    if note.size == 0 or w.sum() <= 0:
        return {t: float("nan") for t in thresholds_bp}
    gap = rate_gap(market_rate_pct, note)
    total = float(w.sum())
    return {t: float(w[gap > t / 100.0].sum() / total) for t in thresholds_bp}


def geography_exposure(
    market_rate_pct: float,
    note_rate_pct: FloatArray,
    current_upb: FloatArray,
    remaining_term_months: FloatArray,
    thresholds_bp: tuple[int, ...] = DEFAULT_THRESHOLDS_BP,
    refi_incentive_threshold_bp: int = 50,
) -> ExposureResult:
    """Full geography-level exposure summary at one date, both weightings.

    All inputs must be the *active* loans in the geography as of the date, with
    the market rate aligned point-in-time.
    """
    note = np.asarray(note_rate_pct, dtype=float)
    upb = np.asarray(current_upb, dtype=float)
    term = np.asarray(remaining_term_months, dtype=float)

    valid = np.isfinite(note) & np.isfinite(upb) & (upb > 0) & np.isfinite(term) & (term > 0)
    note, upb, term = note[valid], upb[valid], term[valid]

    if note.size == 0:
        nan = float("nan")
        return ExposureResult(
            0,
            0.0,
            nan,
            nan,
            float(market_rate_pct),
            dict.fromkeys(thresholds_bp, nan),
            dict.fromkeys(thresholds_bp, nan),
            nan,
            nan,
            nan,
            nan,
        )

    pgap = payment_gap(upb, note, market_rate_pct, term)
    lgap = lockin_gap(market_rate_pct, note)
    rinc = refi_incentive(market_rate_pct, note)

    return ExposureResult(
        n_loans=int(note.size),
        total_upb=float(upb.sum()),
        wavg_note_rate_count=float(note.mean()),
        wavg_note_rate_upb=float(np.average(note, weights=upb)),
        market_rate=float(market_rate_pct),
        locked_share_count=locked_in_share(market_rate_pct, note, None, thresholds_bp),
        locked_share_upb=locked_in_share(market_rate_pct, note, upb, thresholds_bp),
        mean_lockin_gap=float(lgap.mean()),
        median_payment_gap=float(np.median(pgap)),
        mean_payment_gap=float(pgap.mean()),
        refi_incentive_share=float((rinc > refi_incentive_threshold_bp / 100.0).mean()),
    )


# ---------------------------------------------------------------------------
# Bucketing used for descriptive tables and the nonlinear hazard profile
# ---------------------------------------------------------------------------

#: Rate-gap bucket edges in basis points. ``-inf`` .. ``+inf`` closed at the ends.
GAP_BUCKET_EDGES_BP: Final[tuple[float, ...]] = (
    -np.inf,
    -200.0,
    -100.0,
    0.0,
    100.0,
    200.0,
    300.0,
    400.0,
    np.inf,
)
GAP_BUCKET_LABELS: Final[tuple[str, ...]] = (
    "gap < -200bp (strong refi incentive)",
    "-200 to -100bp",
    "-100 to 0bp",
    "0 to +100bp",
    "+100 to +200bp",
    "+200 to +300bp",
    "+300 to +400bp",
    "gap > +400bp (deeply locked in)",
)


def gap_bucket(rate_gap_pct: Numeric) -> npt.NDArray[np.int64]:
    """Assign each rate gap (in percentage points) to a bucket index."""
    bp = np.asarray(rate_gap_pct, dtype=float) * 100.0
    idx = np.digitize(bp, np.asarray(GAP_BUCKET_EDGES_BP[1:-1], dtype=float), right=False)
    return np.asarray(idx, dtype=np.int64)


def gap_bucket_label(idx: int) -> str:
    """Human-readable label for a bucket index from :func:`gap_bucket`."""
    return GAP_BUCKET_LABELS[idx]


MEASURE_REGISTRY: Final[dict[str, str]] = {
    "rate_gap": "Measure 1: market rate minus note rate (pp). Positive = locked in.",
    "lockin_gap": "Measure 2: max(market - note, 0) (pp).",
    "refi_incentive": "Measure 3: note minus market rate (pp). Positive = refi pays.",
    "payment_gap": "Measure 4: monthly P&I change if the remaining balance were "
    "refinanced at the market rate over the remaining term ($/month).",
    "pv_financing_gap": "Measure 5: PV of the payment gap over a calibrated holding "
    "period ($). Calibrated inputs: holding period, discount rate.",
    "locked_share_count": "Measure 8: loan-count-weighted share of active loans with "
    "rate gap above a threshold.",
    "locked_share_upb": "Measure 7: UPB-weighted share of active loans with rate gap "
    "above a threshold.",
    "mean_lockin_gap": "Measure 6 companion: mean positive lock-in gap in the geography.",
}
