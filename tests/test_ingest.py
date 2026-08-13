"""Parsers, schema verification, and the origination↔performance join.

These run entirely on small in-memory fixtures so they are fast and need no data.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from lockin.ingest import origination as orig_mod
from lockin.ingest import performance as perf_mod
from lockin.schemas.freddie import (
    ORIGINATION_COLUMNS,
    PERFORMANCE_COLUMNS,
    SCHEMA_VERSION,
    VERIFIED_AGAINST,
    assert_layout_verified,
    origination_field,
    performance_field,
)


class TestSchema:
    def test_layout_invariants(self) -> None:
        assert_layout_verified()

    def test_field_counts_match_the_official_layout(self) -> None:
        assert len(ORIGINATION_COLUMNS) == 32
        assert len(PERFORMANCE_COLUMNS) == 32

    def test_schema_records_what_it_was_verified_against(self) -> None:
        assert "file_layout.xlsx" in VERIFIED_AGAINST
        assert "user_guide.pdf" in VERIFIED_AGAINST
        assert VERIFIED_AGAINST["verified_on"]
        assert SCHEMA_VERSION.startswith("freddie-llds-")

    def test_sentinel_missing_values_are_recorded(self) -> None:
        """These are the official 'Not Available' codes; forgetting one silently
        turns 9999 into a credit score."""
        assert "9999" in origination_field("credit_score").na_values
        assert "999" in origination_field("orig_dti").na_values
        assert "999" in origination_field("orig_ltv").na_values
        assert "999" in origination_field("orig_cltv").na_values
        assert "99" in origination_field("num_units").na_values
        assert "9" in origination_field("occupancy_status").na_values

    def test_key_positions(self) -> None:
        assert origination_field("loan_seq_no").position == 20
        assert origination_field("orig_interest_rate").position == 13
        assert origination_field("property_state").position == 17
        assert performance_field("loan_seq_no").position == 1
        assert performance_field("zero_balance_code").position == 9
        assert performance_field("current_upb").position == 3
        assert performance_field("loan_age").position == 5

    def test_postal_code_documents_its_truncation(self) -> None:
        note = origination_field("postal_code").note
        assert "three digits" in note
        assert "cannot be used to identify a property" in note


def _orig_line(**over: object) -> str:
    """One schema-exact origination record, pipe-delimited."""
    f = {
        "credit_score": "760",
        "first_payment_date": "202102",
        "first_time_homebuyer_flag": "N",
        "maturity_date": "205101",
        "msa_code": "",
        "mi_percent": "0",
        "num_units": "1",
        "occupancy_status": "P",
        "orig_cltv": "75",
        "orig_dti": "34",
        "orig_upb": "250000",
        "orig_ltv": "75",
        "orig_interest_rate": "2.875",
        "channel": "R",
        "ppm_flag": "N",
        "amortization_type": "FRM",
        "property_state": "CA",
        "property_type": "SF",
        "postal_code": "94100",
        "loan_seq_no": "F20Q40000001",
        "loan_purpose": "P",
        "orig_loan_term": "360",
        "num_borrowers": "02",
        "seller_name": "BIG BANK CORP",
        "servicer_name": "BIG BANK SERVICING",
        "super_conforming_flag": "",
        "pre_relief_refi_loan_seq_no": "",
        "special_eligibility_program": "9",
        "relief_refi_indicator": "",
        "property_valuation_method": "2",
        "interest_only_indicator": "N",
        "mi_cancellation_indicator": "",
    }
    f.update({k: str(v) for k, v in over.items()})
    return "|".join(str(f[c]) for c in ORIGINATION_COLUMNS)


def _perf_line(**over: object) -> str:
    f = dict.fromkeys(PERFORMANCE_COLUMNS, "")
    f.update(
        {
            "loan_seq_no": "F20Q40000001",
            "monthly_reporting_period": "202201",
            "current_upb": "240000.00",
            "delinquency_status": "0",
            "loan_age": "12",
            "remaining_months_to_maturity": "348",
            "current_interest_rate": "2.875",
            "current_deferred_upb": "0",
            "ddlpi": "202112",
            "reported_eltv": "70",
            "interest_bearing_upb": "240000.00",
        }
    )
    f.update({k: str(v) for k, v in over.items()})
    return "|".join(str(f[c]) for c in PERFORMANCE_COLUMNS)


class TestOriginationParser:
    def test_types_and_dates(self) -> None:
        df = orig_mod.parse_chunk([_orig_line()])
        assert df.height == 1
        r = df.row(0, named=True)
        assert r["credit_score"] == 760
        assert r["first_payment_date"] == date(2021, 2, 1)
        assert r["maturity_date"] == date(2051, 1, 1)
        assert r["orig_interest_rate"] == pytest.approx(2.875)
        assert r["orig_upb"] == pytest.approx(250_000.0)
        assert r["property_state"] == "CA"
        assert r["orig_loan_term"] == 360

    def test_sentinels_become_null(self) -> None:
        df = orig_mod.parse_chunk(
            [
                _orig_line(
                    credit_score=9999,
                    orig_dti=999,
                    orig_ltv=999,
                    orig_cltv=999,
                    num_units=99,
                    occupancy_status="9",
                    first_time_homebuyer_flag="9",
                )
            ]
        )
        r = df.row(0, named=True)
        for col in (
            "credit_score",
            "orig_dti",
            "orig_ltv",
            "orig_cltv",
            "num_units",
            "occupancy_status",
            "first_time_homebuyer_flag",
        ):
            assert r[col] is None, f"{col} sentinel was not normalised to null"

    def test_blank_fields_become_null(self) -> None:
        r = orig_mod.parse_chunk([_orig_line()]).row(0, named=True)
        assert r["msa_code"] is None
        assert r["super_conforming_flag"] is None
        assert r["pre_relief_refi_loan_seq_no"] is None

    def test_ragged_line_does_not_crash(self) -> None:
        """Real files contain the occasional truncated row.

        The layout variant is chosen from the MODAL field count over the head of the
        chunk, not from ``lines[0]``, so one short row cannot select the wrong variant
        for every row behind it.
        """
        short = "|".join(["760", "202102", "N"])
        df = orig_mod.parse_chunk([_orig_line(), short])
        assert df.height == 2
        # Every documented column, plus the layout_variant provenance column.
        assert set(ORIGINATION_COLUMNS).issubset(df.columns)
        assert df["layout_variant"][0] == "documented_32_32"

    def test_a_ragged_first_line_does_not_select_the_wrong_variant(self) -> None:
        short = "|".join(["760", "202102", "N"])
        df = orig_mod.parse_chunk([short, _orig_line(), _orig_line()])
        assert df["layout_variant"][0] == "documented_32_32"

    def test_observed_31_field_layout_is_parsed_with_its_own_names(self) -> None:
        """The 2026 full set ships 31 origination fields, not the documented 32.

        Fields 1-24 agree; after that the documented names would be off by one, so a
        value of "N" would land in ``servicer_name``. See lockin.schemas.variants.
        """
        parts = _orig_line().split("|")
        # Drop servicer name (25) and MI cancellation (32), as the shipped files do.
        shipped = parts[:24] + parts[25:31] + ["9999"]
        assert len(shipped) == 31
        df = orig_mod.parse_chunk(["|".join(shipped)])
        assert df["layout_variant"][0] == "observed_31_35"
        assert df["seller_name"][0] == parts[23]
        # Moved to the performance file, so absent here -- null, not mislabelled.
        assert df["servicer_name"][0] is None

    def test_multiple_records(self) -> None:
        lines = [_orig_line(loan_seq_no=f"F20Q4000000{i}") for i in range(1, 6)]
        df = orig_mod.parse_chunk(lines)
        assert df.height == 5
        assert df["loan_seq_no"].n_unique() == 5


class TestPerformanceParser:
    def test_projection_and_types(self) -> None:
        df = perf_mod.parse_chunk([_perf_line()])
        assert set(df.columns) == set(perf_mod.KEEP)
        r = df.row(0, named=True)
        assert r["monthly_reporting_period"] == date(2022, 1, 1)
        assert r["current_upb"] == pytest.approx(240_000.0)
        assert r["loan_age"] == 12
        assert r["remaining_months_to_maturity"] == 348
        assert r["zero_balance_code"] is None

    def test_loss_columns_are_dropped(self) -> None:
        """The loss/expense block roughly halves the on-disk footprint."""
        df = perf_mod.parse_chunk([_perf_line()])
        for dropped in (
            "mi_recoveries",
            "net_sales_proceeds",
            "actual_loss",
            "legal_costs",
            "taxes_and_insurance",
        ):
            assert dropped not in df.columns

    def test_zero_balance_row(self) -> None:
        df = perf_mod.parse_chunk(
            [
                _perf_line(
                    current_upb="0.00",
                    zero_balance_code="01",
                    zero_balance_effective_date="202206",
                    monthly_reporting_period="202206",
                )
            ]
        )
        r = df.row(0, named=True)
        assert r["zero_balance_code"] == "01"
        assert r["zero_balance_effective_date"] == date(2022, 6, 1)
        assert r["current_upb"] == pytest.approx(0.0)

    def test_modification_flag_values(self) -> None:
        for flag in ("Y", "P"):
            r = perf_mod.parse_chunk([_perf_line(modification_flag=flag)]).row(0, named=True)
            assert r["modification_flag"] == flag
        r = perf_mod.parse_chunk([_perf_line()]).row(0, named=True)
        assert r["modification_flag"] is None


class TestJoin:
    def test_join_is_deterministic_and_keyed_on_loan_seq_no(self) -> None:
        """Acceptance criterion 4. The join must be reproducible and must not
        duplicate loans."""
        orig = orig_mod.parse_chunk(
            [
                _orig_line(loan_seq_no="F20Q40000001"),
                _orig_line(loan_seq_no="F20Q40000002", property_state="TX"),
            ]
        )
        perf = perf_mod.parse_chunk(
            [
                _perf_line(loan_seq_no="F20Q40000001", monthly_reporting_period="202201"),
                _perf_line(
                    loan_seq_no="F20Q40000001", monthly_reporting_period="202202", loan_age="13"
                ),
                _perf_line(loan_seq_no="F20Q40000002", monthly_reporting_period="202201"),
            ]
        )
        j1 = perf.join(orig, on="loan_seq_no", how="inner").sort(
            ["loan_seq_no", "monthly_reporting_period"]
        )
        j2 = perf.join(orig, on="loan_seq_no", how="inner").sort(
            ["loan_seq_no", "monthly_reporting_period"]
        )
        assert j1.equals(j2)
        assert j1.height == 3
        assert j1.filter(pl.col("loan_seq_no") == "F20Q40000001").height == 2
        assert j1["property_state"].to_list() == ["CA", "CA", "TX"]

    def test_performance_rows_without_origination_are_dropped_by_inner_join(self) -> None:
        orig = orig_mod.parse_chunk([_orig_line(loan_seq_no="F20Q40000001")])
        perf = perf_mod.parse_chunk(
            [
                _perf_line(loan_seq_no="F20Q40000001"),
                _perf_line(loan_seq_no="F20Q49999999"),
            ]
        )
        j = perf.join(orig, on="loan_seq_no", how="inner")
        assert j.height == 1


class TestLayoutYaml:
    """The committed YAML field map must never drift from the schema module."""

    def test_layout_yaml_matches_schema(self) -> None:
        import yaml

        from lockin.config import REPO_ROOT
        from lockin.schemas.freddie import layout_spec

        p = REPO_ROOT / "data" / "reference" / "freddie_llds_layout.yaml"
        assert p.exists(), (
            "data/reference/freddie_llds_layout.yaml is missing. It is referenced by "
            "docs/DECISION_LOG.md D003 and data/LICENSE_AND_REDISTRIBUTION.md. "
            "Regenerate it with `make emit-layout`."
        )
        on_disk = yaml.safe_load(p.read_text())
        assert on_disk == layout_spec(), (
            "the committed layout YAML disagrees with lockin.schemas.freddie. "
            "Run `make emit-layout` -- do not hand-edit the YAML."
        )

    def test_layout_yaml_documents_the_zb_priority_table(self) -> None:
        import yaml

        from lockin.config import REPO_ROOT

        spec = yaml.safe_load(
            (REPO_ROOT / "data" / "reference" / "freddie_llds_layout.yaml").read_text()
        )
        zb = {z["code"]: z for z in spec["zero_balance_codes"]}
        assert [z["code"] for z in spec["zero_balance_codes"]] == [
            "15",
            "16",
            "09",
            "96",
            "03",
            "02",
            "01",
        ], "zero_balance_codes must be listed in official priority order"
        assert zb["01"]["event_class"] == "prepayment"
        assert zb["15"]["treated_as_censoring"] is True
        assert zb["02"]["event_class"] == "credit_event"
