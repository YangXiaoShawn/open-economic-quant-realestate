"""Loan-event construction: the outcome taxonomy and the survival invariants.

The tests here are the mechanical enforcement of ``AGENTS.md`` §1. The most
important one is :func:`test_no_home_sale_or_move_event_exists` -- if a future
change introduces a "sale" or "move" event class, this suite fails.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from lockin.events import (
    EVENT_TYPES,
    event_summary,
    maturity_like_prepayments,
    validate_events,
)
from lockin.schemas.freddie import (
    CENSORING_ZB_CODES,
    CREDIT_EVENT_ZB_CODES,
    PREPAYMENT_ZB_CODES,
    ZERO_BALANCE_CODES,
    classify_zero_balance,
)


class TestZeroBalanceTaxonomy:
    def test_official_codes_and_priorities(self) -> None:
        """Transcribed from the official user guide's termination-event table."""
        assert set(ZERO_BALANCE_CODES) == {"01", "02", "03", "09", "15", "16", "96"}
        assert ZERO_BALANCE_CODES["15"].priority == 1
        assert ZERO_BALANCE_CODES["16"].priority == 2
        assert ZERO_BALANCE_CODES["09"].priority == 3
        assert ZERO_BALANCE_CODES["96"].priority == 4
        assert ZERO_BALANCE_CODES["03"].priority == 5
        assert ZERO_BALANCE_CODES["02"].priority == 6
        assert ZERO_BALANCE_CODES["01"].priority == 7

    def test_prepayment_is_only_code_01(self) -> None:
        assert {"01"} == PREPAYMENT_ZB_CODES

    def test_credit_events_are_02_03_09(self) -> None:
        assert {"02", "03", "09"} == CREDIT_EVENT_ZB_CODES

    def test_administrative_removals_are_censored(self) -> None:
        """15/16/96 are Freddie Mac portfolio and R&W actions, not borrower decisions."""
        assert {"15", "16", "96"} == CENSORING_ZB_CODES
        for code in ("15", "16", "96"):
            _, censored = classify_zero_balance(code)
            assert censored, f"ZB {code} must be censoring"

    def test_third_party_sale_is_a_credit_event_not_a_sale(self) -> None:
        """ZB 02 is a foreclosure-auction sale. Calling it a household sale is the
        exact error AGENTS.md forbids."""
        cls, censored = classify_zero_balance("02")
        assert cls == "credit_event"
        assert not censored
        assert "credit" in ZERO_BALANCE_CODES["02"].rationale.lower()

    def test_code_01_documents_the_conflation(self) -> None:
        r = ZERO_BALANCE_CODES["01"].rationale.upper()
        assert "CONFLATES" in r
        assert "MATURITY" in r
        assert "REFINANCE" in r

    def test_blank_and_none_are_censored_active(self) -> None:
        for v in (None, "", "   "):
            cls, censored = classify_zero_balance(v)
            assert cls == "censored_active"
            assert censored

    def test_unknown_code_is_censored_never_guessed(self) -> None:
        cls, censored = classify_zero_balance("77")
        assert cls == "unknown_zb_code"
        assert censored

    def test_codes_are_zero_padded_leniently(self) -> None:
        assert classify_zero_balance("1")[0] == "prepayment"
        assert classify_zero_balance("9")[0] == "credit_event"

    def test_event_types_contain_no_sale_or_move(self) -> None:
        assert set(EVENT_TYPES) == {"prepayment", "credit_event", "censored"}


def _events(rows: list[dict]) -> pl.DataFrame:
    """Minimal loan-event frame with the columns the validator needs."""
    base = {
        "loan_seq_no": "F21Q10000001",
        "observation_start": date(2021, 1, 1),
        "observation_end": date(2023, 12, 1),
        "start_age": 1,
        "end_age": 36,
        "event_type": "censored",
        "event_date": None,
        "censored": True,
        "zero_balance_code": None,
        "n_month_gaps": 0,
        "ever_modified": False,
        "conflicting_zb_codes": False,
        "reappeared_after_exit": False,
        "home_sale_observed": False,
        "orig_upb": 250_000.0,
        "maturity_date": date(2051, 2, 1),
    }
    return pl.DataFrame([{**base, **r} for r in rows])


class TestValidationInvariants:
    def test_clean_frame_has_no_hard_problems(self) -> None:
        ev = _events(
            [
                {
                    "loan_seq_no": "A",
                    "event_type": "prepayment",
                    "event_date": date(2022, 6, 1),
                    "censored": False,
                    "zero_balance_code": "01",
                },
                {
                    "loan_seq_no": "B",
                    "event_type": "credit_event",
                    "event_date": date(2023, 1, 1),
                    "censored": False,
                    "zero_balance_code": "03",
                },
                {"loan_seq_no": "C"},
                {
                    "loan_seq_no": "D",
                    "event_type": "censored",
                    "event_date": date(2022, 9, 1),
                    "censored": True,
                    "zero_balance_code": "15",
                },
            ]
        )
        problems = validate_events(ev)
        assert [p for p in problems if p.startswith("HARD")] == []

    def test_duplicate_loans_are_hard(self) -> None:
        ev = _events([{"loan_seq_no": "A"}, {"loan_seq_no": "A"}])
        assert any("duplicate loans" in p for p in validate_events(ev) if p.startswith("HARD"))

    def test_exit_before_observation_start_is_hard(self) -> None:
        ev = _events(
            [
                {
                    "loan_seq_no": "A",
                    "event_type": "prepayment",
                    "censored": False,
                    "zero_balance_code": "01",
                    "event_date": date(2020, 6, 1),
                }
            ]
        )
        assert any("exit before observation_start" in p for p in validate_events(ev))

    def test_observation_end_before_start_is_hard(self) -> None:
        ev = _events([{"loan_seq_no": "A", "observation_end": date(2020, 1, 1)}])
        assert any("observation_end < observation_start" in p for p in validate_events(ev))

    def test_admin_removal_not_censored_is_hard(self) -> None:
        """The single most important leak to catch: a portfolio action counted as
        borrower behaviour."""
        ev = _events(
            [
                {
                    "loan_seq_no": "A",
                    "zero_balance_code": "16",
                    "event_type": "prepayment",
                    "censored": False,
                    "event_date": date(2022, 6, 1),
                }
            ]
        )
        hard = [p for p in validate_events(ev) if p.startswith("HARD")]
        assert any("administrative-removal" in p for p in hard)

    def test_no_home_sale_or_move_event_exists(self) -> None:
        """If anything ever sets home_sale_observed, the pipeline must fail."""
        ev = _events([{"loan_seq_no": "A", "home_sale_observed": True}])
        hard = [p for p in validate_events(ev) if p.startswith("HARD")]
        assert any("home_sale_observed" in p for p in hard)

    def test_unknown_event_type_is_hard(self) -> None:
        ev = _events([{"loan_seq_no": "A", "event_type": "home_sale"}])
        assert any("event_type outside" in p for p in validate_events(ev))

    def test_empty_frame_is_hard(self) -> None:
        assert validate_events(_events([]).head(0))[0].startswith("HARD")

    def test_left_truncation_is_reported_as_info(self) -> None:
        ev = _events([{"loan_seq_no": "A", "start_age": 14}])
        assert any("LEFT TRUNCATED" in p for p in validate_events(ev))

    def test_conflicting_and_reappearing_are_soft(self) -> None:
        ev = _events(
            [
                {"loan_seq_no": "A", "conflicting_zb_codes": True},
                {"loan_seq_no": "B", "reappeared_after_exit": True},
            ]
        )
        problems = validate_events(ev)
        assert [p for p in problems if p.startswith("HARD")] == []
        assert any("more than one Zero Balance Code" in p for p in problems)
        assert any("after their exit month" in p for p in problems)


class TestEventSummary:
    def test_summary_partitions_the_population(self) -> None:
        ev = _events(
            [
                {
                    "loan_seq_no": "A",
                    "event_type": "prepayment",
                    "censored": False,
                    "zero_balance_code": "01",
                    "event_date": date(2022, 6, 1),
                },
                {
                    "loan_seq_no": "B",
                    "event_type": "credit_event",
                    "censored": False,
                    "zero_balance_code": "09",
                    "event_date": date(2023, 2, 1),
                },
                {"loan_seq_no": "C"},
            ]
        )
        s = event_summary(ev)
        assert s["n_loans"] == 3
        assert sum(int(r["n_loans"]) for r in s["by_event_type"]) == 3

    def test_summary_states_the_conflation_in_its_definitions(self) -> None:
        s = event_summary(_events([{"loan_seq_no": "A"}]))
        d = s["outcome_definitions"]["prepayment"].upper()
        assert "CONFLATES" in d
        assert "NOT A HOME SALE" in d
        assert "NOT A HOUSEHOLD MOVE" in d


class TestMaturityHeuristic:
    def test_flags_prepayments_near_scheduled_maturity(self) -> None:
        ev = _events(
            [
                # Prepaid one month before maturity -> maturity-like.
                {
                    "loan_seq_no": "A",
                    "event_type": "prepayment",
                    "censored": False,
                    "zero_balance_code": "01",
                    "event_date": date(2051, 1, 1),
                    "maturity_date": date(2051, 2, 1),
                },
                # Prepaid 29 years early -> a genuine voluntary payoff.
                {
                    "loan_seq_no": "B",
                    "event_type": "prepayment",
                    "censored": False,
                    "zero_balance_code": "01",
                    "event_date": date(2022, 6, 1),
                    "maturity_date": date(2051, 2, 1),
                },
            ]
        )
        assert maturity_like_prepayments(ev, threshold_months=3) == 1

    def test_it_is_a_heuristic_not_a_classification(self) -> None:
        """Censored loans are never counted, whatever their maturity date."""
        ev = _events(
            [
                {
                    "loan_seq_no": "A",
                    "event_date": date(2051, 1, 1),
                    "maturity_date": date(2051, 2, 1),
                }
            ]
        )
        assert maturity_like_prepayments(ev) == 0


@pytest.mark.parametrize("code", ["01", "02", "03", "09", "15", "16", "96"])
def test_every_documented_code_maps_to_a_known_class(code: str) -> None:
    cls, censored = classify_zero_balance(code)
    assert cls in {"prepayment", "credit_event", "admin_removal"}
    assert censored == ZERO_BALANCE_CODES[code].censoring
