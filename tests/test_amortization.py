"""Mortgage amortization: closed-form identities, edge cases, and the Polars mirror."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from lockin.amortization import (
    amortization_schedule,
    annuity_factor,
    monthly_rate,
    payment,
    remaining_balance,
    remaining_term,
    total_interest,
)


def test_payment_known_value() -> None:
    """$200,000 at 6% for 360 months. Hand-checked against the closed form."""
    got = float(payment(200_000.0, 6.0, 360))
    i = 0.06 / 12
    want = 200_000.0 * i * (1 + i) ** 360 / ((1 + i) ** 360 - 1)
    assert got == pytest.approx(want, rel=1e-12)
    assert got == pytest.approx(1199.10, abs=0.01)


def test_payment_zero_rate_is_exact_linear() -> None:
    """A 0% loan amortises linearly; no epsilon fudge is used."""
    assert float(payment(120_000.0, 0.0, 120)) == pytest.approx(1000.0, rel=1e-12)


def test_payment_monotone_in_rate_and_principal() -> None:
    rates = np.array([2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    pmts = payment(np.full(rates.size, 300_000.0), rates, np.full(rates.size, 360.0))
    assert np.all(np.diff(pmts) > 0)
    p = payment(np.array([100_000.0, 200_000.0, 400_000.0]), 5.0, 360.0)
    assert np.all(np.diff(p) > 0)
    # Payment is exactly linear in principal.
    assert p[1] == pytest.approx(2 * p[0], rel=1e-12)
    assert p[2] == pytest.approx(4 * p[0], rel=1e-12)


def test_payment_non_positive_term_is_nan_not_zero() -> None:
    """Returning 0 would silently corrupt payment-gap aggregates."""
    assert np.isnan(float(payment(100_000.0, 5.0, 0)))
    assert np.isnan(float(payment(100_000.0, 5.0, -12)))


def test_remaining_balance_endpoints() -> None:
    p, r, n = 250_000.0, 5.5, 360
    assert float(remaining_balance(p, r, n, 0)) == pytest.approx(p, rel=1e-12)
    assert float(remaining_balance(p, r, n, n)) == pytest.approx(0.0, abs=1e-6)
    assert float(remaining_balance(p, r, n, n + 24)) == 0.0


def test_remaining_balance_is_monotone_decreasing() -> None:
    k = np.arange(0, 361, 12, dtype=float)
    bal = remaining_balance(300_000.0, 6.5, 360.0, k)
    assert np.all(np.diff(bal) < 0)


def test_amortization_schedule_reconciles() -> None:
    """Interest + principal must equal the payment in every month, and the final
    balance must be zero. This is the identity that catches sign and index errors."""
    p, r, n = 180_000.0, 4.25, 240
    s = amortization_schedule(p, r, n)
    assert np.allclose(s["interest"] + s["principal"], s["payment"], rtol=1e-10)
    assert np.allclose(s["balance_end"], s["balance_start"] - s["principal"], rtol=1e-9, atol=1e-6)
    assert s["balance_end"][-1] == pytest.approx(0.0, abs=1e-5)
    assert s["principal"].sum() == pytest.approx(p, rel=1e-9)


def test_total_interest_matches_schedule() -> None:
    p, r, n = 180_000.0, 4.25, 240
    s = amortization_schedule(p, r, n)
    assert float(total_interest(p, r, n)) == pytest.approx(s["interest"].sum(), rel=1e-9)


def test_annuity_factor_identities() -> None:
    # Zero discount: the factor is just the number of months.
    assert float(annuity_factor(84, 0.0)) == pytest.approx(84.0, rel=1e-12)
    # Positive discount: strictly less than the month count, and increasing in months.
    a = annuity_factor(np.array([12.0, 60.0, 120.0]), 4.0)
    assert np.all(a < np.array([12.0, 60.0, 120.0]))
    assert np.all(np.diff(a) > 0)
    # Closed form.
    d = 0.04 / 12
    assert float(annuity_factor(84, 4.0)) == pytest.approx((1 - (1 + d) ** -84) / d, rel=1e-12)


def test_annuity_factor_prices_a_level_annuity() -> None:
    """PV of the payment stream at the note rate equals the original principal."""
    p, r, n = 200_000.0, 6.0, 360
    pmt = float(payment(p, r, n))
    assert pmt * float(annuity_factor(n, r)) == pytest.approx(p, rel=1e-9)


def test_monthly_rate_convention() -> None:
    """U.S. mortgage convention: simple division by 12, not a geometric conversion."""
    assert float(monthly_rate(6.0)) == pytest.approx(0.005, rel=1e-15)


def test_remaining_term_floors() -> None:
    assert float(remaining_term(360, 12)) == 348.0
    assert float(remaining_term(360, 400)) == 1.0
    assert float(remaining_term(360, 400, floor_months=0)) == 0.0


def test_polars_expression_matches_numpy() -> None:
    """`lockin.episodes._pmt_expr` runs inside the lazy plan and must agree exactly
    with the NumPy implementation, including at a zero coupon."""
    from lockin.episodes import _pmt_expr

    df = pl.DataFrame(
        {
            "bal": [100_000.0, 250_000.0, 400_000.0, 50_000.0, 123_456.0],
            "rate": [3.0, 6.5, 7.125, 0.0, 4.875],
            "n": [360.0, 240.0, 180.0, 120.0, 301.0],
        }
    )
    got = df.select(_pmt_expr(pl.col("bal"), pl.col("rate"), pl.col("n")).alias("pmt"))[
        "pmt"
    ].to_numpy()
    want = payment(df["bal"].to_numpy(), df["rate"].to_numpy(), df["n"].to_numpy())
    assert np.allclose(got, want, rtol=1e-12)


def test_polars_stock_pmt_matches_numpy() -> None:
    """The exposure builder has its own Polars payment expression; pin it too."""
    from lockin.stock import _pmt

    df = pl.DataFrame({"bal": [200_000.0, 75_000.0], "rate": [5.25, 0.0], "n": [300.0, 96.0]})
    got = df.select(_pmt(pl.col("bal"), pl.col("rate"), pl.col("n")).alias("p"))["p"].to_numpy()
    want = payment(df["bal"].to_numpy(), df["rate"].to_numpy(), df["n"].to_numpy())
    assert np.allclose(got, want, rtol=1e-12)
