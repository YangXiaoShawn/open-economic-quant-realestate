"""Level-payment mortgage amortization.

Pure functions, no I/O, fully unit-tested. Every rate argument is an **annual
percentage** (e.g. ``6.875`` for 6.875%), matching the Freddie Mac
``Original Interest Rate`` field, and every term is in **months**.

The zero-rate limit is handled exactly rather than by an epsilon fudge, because a
0% coupon does appear in synthetic tests and in some subsidised programs.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
Numeric = float | FloatArray


def monthly_rate(annual_rate_pct: Numeric) -> Numeric:
    """Convert an annual percentage rate to a monthly decimal rate.

    Uses the U.S. mortgage convention of simple division by 12 (not a geometric
    conversion), which is what note-rate amortization schedules use.
    """
    return np.asarray(annual_rate_pct, dtype=float) / 1200.0


def payment(principal: Numeric, annual_rate_pct: Numeric, term_months: Numeric) -> FloatArray:
    r"""Level monthly principal-and-interest payment.

    .. math::
        \mathrm{PMT} = P \cdot \frac{i(1+i)^n}{(1+i)^n - 1},
        \qquad i = \frac{r}{1200},\ n = \text{term\_months}

    With :math:`i = 0` this reduces to :math:`P / n`.

    ``term_months <= 0`` yields ``nan`` -- a loan with no remaining payments has
    no defined level payment, and silently returning 0 would corrupt payment-gap
    aggregates.
    """
    p = np.asarray(principal, dtype=float)
    i = np.asarray(monthly_rate(annual_rate_pct), dtype=float)
    n = np.asarray(term_months, dtype=float)

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        growth = np.power(1.0 + i, n)
        amortized = p * i * growth / (growth - 1.0)
        linear = p / n
        out = np.where(np.isclose(i, 0.0), linear, amortized)
        out = np.where(n > 0, out, np.nan)
    return np.asarray(out, dtype=float)


def remaining_balance(
    principal: Numeric,
    annual_rate_pct: Numeric,
    term_months: Numeric,
    months_elapsed: Numeric,
) -> FloatArray:
    r"""Scheduled remaining balance after ``months_elapsed`` payments.

    .. math::
        B_k = P\,\frac{(1+i)^n - (1+i)^k}{(1+i)^n - 1}

    Assumes no curtailments and no missed payments. For observed data prefer the
    reported ``current_upb``; this is for the counterfactual arm of the
    payment-gap calculation and for fixture generation.
    """
    p = np.asarray(principal, dtype=float)
    i = np.asarray(monthly_rate(annual_rate_pct), dtype=float)
    n = np.asarray(term_months, dtype=float)
    k = np.clip(np.asarray(months_elapsed, dtype=float), 0.0, None)

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        gn = np.power(1.0 + i, n)
        gk = np.power(1.0 + i, k)
        amortized = p * (gn - gk) / (gn - 1.0)
        linear = p * (1.0 - k / n)
        out = np.where(np.isclose(i, 0.0), linear, amortized)
    out = np.where(k >= n, 0.0, out)
    out = np.where(n > 0, out, np.nan)
    return np.asarray(np.clip(out, 0.0, None), dtype=float)


def remaining_term(orig_loan_term: Numeric, loan_age: Numeric, floor_months: int = 1) -> FloatArray:
    """Remaining scheduled months, floored at ``floor_months``.

    Freddie Mac supplies ``Remaining Months to Legal Maturity`` directly; prefer
    it. This is the fallback when only origination term and loan age are known.
    """
    n = np.asarray(orig_loan_term, dtype=float)
    a = np.asarray(loan_age, dtype=float)
    return np.asarray(np.clip(n - a, float(floor_months), None), dtype=float)


def annuity_factor(months: Numeric, annual_discount_pct: Numeric) -> FloatArray:
    r"""Present value of 1 per month for ``months`` months.

    .. math:: a_{H,\delta} = \frac{1 - (1+\delta)^{-H}}{\delta},
        \qquad \delta = \frac{d}{1200}

    With :math:`\delta = 0` this is just :math:`H`.
    """
    h = np.asarray(months, dtype=float)
    d = np.asarray(monthly_rate(annual_discount_pct), dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        disc = (1.0 - np.power(1.0 + d, -h)) / d
        out = np.where(np.isclose(d, 0.0), h, disc)
    return np.asarray(np.clip(out, 0.0, None), dtype=float)


def total_interest(
    principal: Numeric, annual_rate_pct: Numeric, term_months: Numeric
) -> FloatArray:
    """Total nominal interest paid over the full scheduled term."""
    pmt = payment(principal, annual_rate_pct, term_months)
    n = np.asarray(term_months, dtype=float)
    p = np.asarray(principal, dtype=float)
    return np.asarray(pmt * n - p, dtype=float)


def amortization_schedule(
    principal: float, annual_rate_pct: float, term_months: int
) -> dict[str, FloatArray]:
    """Full month-by-month schedule. Small-n helper for tests and documentation."""
    if term_months <= 0:
        raise ValueError("term_months must be positive")
    k = np.arange(0, term_months + 1, dtype=float)
    bal = remaining_balance(principal, annual_rate_pct, term_months, k)
    pmt = float(payment(principal, annual_rate_pct, term_months))
    interest = bal[:-1] * float(monthly_rate(annual_rate_pct))
    principal_paid = pmt - interest
    return {
        "month": k[1:],
        "balance_start": bal[:-1],
        "balance_end": bal[1:],
        "payment": np.full(term_months, pmt),
        "interest": interest,
        "principal": principal_paid,
    }
