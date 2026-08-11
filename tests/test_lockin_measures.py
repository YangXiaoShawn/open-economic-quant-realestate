"""The eight lock-in measures: sign conventions, identities, and edge cases."""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest

from lockin.amortization import annuity_factor, payment
from lockin.lockin_measures import (
    DEFAULT_THRESHOLDS_BP,
    GAP_BUCKET_LABELS,
    gap_bucket,
    gap_bucket_label,
    geography_exposure,
    locked_in_share,
    lockin_gap,
    payment_gap,
    payment_gap_fresh_term,
    pv_financing_gap,
    rate_gap,
    refi_incentive,
)


class TestSignConventions:
    """The sign conventions are fixed once and used everywhere; pin them hard."""

    def test_rate_gap_positive_means_locked_in(self) -> None:
        # Market 7%, note 3% -> gap +4pp -> locked in.
        assert float(rate_gap(7.0, 3.0)) == pytest.approx(4.0)
        # Market 3%, note 7% -> gap -4pp -> refinance incentive.
        assert float(rate_gap(3.0, 7.0)) == pytest.approx(-4.0)

    def test_lockin_gap_is_positive_part(self) -> None:
        assert float(lockin_gap(7.0, 3.0)) == pytest.approx(4.0)
        assert float(lockin_gap(3.0, 7.0)) == 0.0

    def test_refi_incentive_is_negated_rate_gap(self) -> None:
        m = np.array([7.0, 3.0, 5.0])
        n = np.array([3.0, 7.0, 5.0])
        assert np.allclose(refi_incentive(m, n), -rate_gap(m, n))

    def test_lockin_and_refi_are_mutually_exclusive(self) -> None:
        """A borrower cannot be both locked in and have a refinance incentive."""
        rng = np.random.default_rng(0)
        m = rng.uniform(2, 9, 500)
        n = rng.uniform(2, 9, 500)
        both = (lockin_gap(m, n) > 0) & (np.clip(refi_incentive(m, n), 0, None) > 0)
        assert not both.any()


class TestPaymentGap:
    def test_payment_gap_equals_payment_difference(self) -> None:
        bal, note, mkt, n = 300_000.0, 3.0, 7.0, 300.0
        got = float(payment_gap(bal, note, mkt, n))
        want = float(payment(bal, mkt, n)) - float(payment(bal, note, n))
        assert got == pytest.approx(want, rel=1e-12)
        assert got > 0  # locked in: refinancing raises the payment

    def test_payment_gap_sign_tracks_rate_gap_sign(self) -> None:
        rng = np.random.default_rng(1)
        bal = rng.uniform(50_000, 700_000, 400)
        note = rng.uniform(2.5, 8.5, 400)
        mkt = rng.uniform(2.5, 8.5, 400)
        n = rng.integers(24, 360, 400).astype(float)
        pg = payment_gap(bal, note, mkt, n)
        rg = rate_gap(mkt, note)
        material = np.abs(rg) > 1e-6
        assert np.all(np.sign(pg[material]) == np.sign(rg[material]))

    def test_payment_gap_zero_when_rates_equal(self) -> None:
        assert float(payment_gap(250_000.0, 5.0, 5.0, 240.0)) == pytest.approx(0.0, abs=1e-9)

    def test_payment_gap_scales_linearly_in_balance(self) -> None:
        a = float(payment_gap(100_000.0, 3.0, 7.0, 300.0))
        b = float(payment_gap(300_000.0, 3.0, 7.0, 300.0))
        assert b == pytest.approx(3 * a, rel=1e-12)

    def test_fresh_term_differs_and_is_documented_direction(self) -> None:
        """Re-extending to a fresh 30-year term lowers the payment relative to
        keeping the (shorter) remaining term, so the fresh-term gap is smaller."""
        bal, note, mkt, rem = 300_000.0, 3.0, 7.0, 240.0
        same = float(payment_gap(bal, note, mkt, rem))
        fresh = float(payment_gap_fresh_term(bal, note, mkt, rem, 360))
        assert fresh < same


class TestPvFinancingGap:
    def test_pv_gap_is_payment_gap_times_annuity_factor(self) -> None:
        bal, note, mkt, n, h, d = 300_000.0, 3.0, 7.0, 300.0, 84, 4.0
        pg = float(payment_gap(bal, note, mkt, n))
        want = pg * float(annuity_factor(min(h, n), d))
        assert float(pv_financing_gap(bal, note, mkt, n, h, d)) == pytest.approx(want, rel=1e-12)

    def test_holding_period_is_capped_at_remaining_term(self) -> None:
        """You cannot pay the differential for longer than the loan exists."""
        bal, note, mkt = 200_000.0, 3.0, 7.0
        short = 36.0
        capped = float(pv_financing_gap(bal, note, mkt, short, 84, 4.0))
        explicit = float(payment_gap(bal, note, mkt, short)) * float(annuity_factor(36, 4.0))
        assert capped == pytest.approx(explicit, rel=1e-12)

    def test_pv_gap_increases_with_holding_period(self) -> None:
        vals = [
            float(pv_financing_gap(300_000.0, 3.0, 7.0, 300.0, h, 4.0)) for h in (12, 36, 84, 180)
        ]
        assert all(b > a for a, b in pairwise(vals))

    def test_zero_discount_is_simple_sum(self) -> None:
        bal, note, mkt, n, h = 250_000.0, 4.0, 6.0, 300.0, 60
        pg = float(payment_gap(bal, note, mkt, n))
        assert float(pv_financing_gap(bal, note, mkt, n, h, 0.0)) == pytest.approx(
            pg * h, rel=1e-12
        )


class TestLockedInShare:
    def test_thresholds_are_nested(self) -> None:
        note = np.array([2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        shares = locked_in_share(7.0, note)
        vals = [shares[t] for t in DEFAULT_THRESHOLDS_BP]
        assert all(b <= a for a, b in pairwise(vals))

    def test_count_weighting_counts_loans_equally(self) -> None:
        # Gaps: +4, +4, +0.5 at a market rate of 7.
        note = np.array([3.0, 3.0, 6.5])
        s = locked_in_share(7.0, note, None, (200,))
        assert s[200] == pytest.approx(2 / 3)

    def test_upb_weighting_weights_by_balance(self) -> None:
        note = np.array([3.0, 3.0, 6.5])
        upb = np.array([100_000.0, 100_000.0, 800_000.0])
        s = locked_in_share(7.0, note, upb, (200,))
        assert s[200] == pytest.approx(200_000 / 1_000_000)

    def test_count_and_upb_disagree_when_balances_are_skewed(self) -> None:
        """This is why both weightings are always reported."""
        note = np.array([3.0, 3.0, 6.5])
        upb = np.array([100_000.0, 100_000.0, 800_000.0])
        assert locked_in_share(7.0, note, None, (200,))[200] != pytest.approx(
            locked_in_share(7.0, note, upb, (200,))[200]
        )

    def test_empty_input_is_nan_not_zero(self) -> None:
        s = locked_in_share(7.0, np.array([]))
        assert all(np.isnan(v) for v in s.values())

    def test_nan_note_rates_are_excluded(self) -> None:
        note = np.array([3.0, np.nan, 6.5])
        s = locked_in_share(7.0, note, None, (200,))
        assert s[200] == pytest.approx(0.5)


class TestGeographyExposure:
    def test_exposure_reports_both_weightings(self) -> None:
        rng = np.random.default_rng(2)
        n = 500
        note = rng.uniform(2.5, 7.5, n)
        upb = rng.uniform(50_000, 600_000, n)
        term = rng.integers(60, 360, n).astype(float)
        r = geography_exposure(7.0, note, upb, term)
        assert r.n_loans == n
        assert set(r.locked_share_count) == set(DEFAULT_THRESHOLDS_BP)
        assert set(r.locked_share_upb) == set(DEFAULT_THRESHOLDS_BP)
        assert 0.0 <= r.locked_share_count[200] <= 1.0
        assert r.total_upb == pytest.approx(upb.sum())

    def test_exposure_drops_zero_balance_loans(self) -> None:
        note = np.array([3.0, 3.0, 3.0])
        upb = np.array([100_000.0, 0.0, 100_000.0])
        term = np.array([300.0, 300.0, 300.0])
        r = geography_exposure(7.0, note, upb, term)
        assert r.n_loans == 2

    def test_exposure_empty_is_nan(self) -> None:
        r = geography_exposure(7.0, np.array([]), np.array([]), np.array([]))
        assert r.n_loans == 0
        assert np.isnan(r.mean_lockin_gap)

    def test_refi_incentive_share_is_separate_from_lockin(self) -> None:
        """When the market rate is far below every coupon, nobody is locked in and
        everybody has a refinance incentive."""
        note = np.array([6.0, 6.5, 7.0])
        upb = np.full(3, 200_000.0)
        term = np.full(3, 300.0)
        r = geography_exposure(3.0, note, upb, term)
        assert r.locked_share_count[100] == 0.0
        assert r.refi_incentive_share == pytest.approx(1.0)


class TestGapBuckets:
    def test_bucket_assignment_and_labels(self) -> None:
        gaps = np.array([-3.0, -1.5, -0.5, 0.5, 1.5, 2.5, 3.5, 5.0])
        idx = gap_bucket(gaps)
        assert idx.tolist() == [0, 1, 2, 3, 4, 5, 6, 7]
        assert gap_bucket_label(0).startswith("gap < -200bp")
        assert gap_bucket_label(7).startswith("gap > +400bp")
        assert len(GAP_BUCKET_LABELS) == 8

    def test_bucket_is_monotone(self) -> None:
        gaps = np.linspace(-6, 8, 200)
        idx = gap_bucket(gaps)
        assert np.all(np.diff(idx) >= 0)

    def test_polars_bucket_matches_numpy(self) -> None:
        import polars as pl

        from lockin.episodes import _gap_bucket_expr

        gaps = np.array([-3.0, -1.5, -0.5, 0.0, 0.5, 1.5, 2.5, 3.5, 5.0])
        df = pl.DataFrame({"gap": gaps})
        got = df.select(_gap_bucket_expr(pl.col("gap")).alias("b"))["b"].to_numpy()
        assert got.tolist() == gap_bucket(gaps).tolist()
