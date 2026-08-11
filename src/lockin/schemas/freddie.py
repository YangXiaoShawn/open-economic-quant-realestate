"""Freddie Mac Single-Family Loan-Level Dataset schema.

VERIFIED against the official public documentation on 2026-08-10:

* ``https://www.freddiemac.com/fmac-resources/research/pdf/file_layout.xlsx``
  -- field positions, attribute names, data types, max lengths.
* ``https://www.freddiemac.com/fmac-resources/research/pdf/user_guide.pdf``
  -- formal definitions, valid values, sentinel missing-value codes, and the
  Zero Balance Code termination-event priority table.

Both documents are served **without** registration. The *data* files are not; see
``data/DATA_ACCESS.md``.

Layout at verification time: **32 origination fields**, **32 monthly performance
fields**, pipe-delimited, no header row.

Known documentation discrepancies (see ``docs/DECISION_LOG.md`` D003):

* Performance position 12 is "Current Deferred UPB" in the layout spreadsheet and
  "Current Non-Interest Bearing UPB" in the user guide. Same field.
* Performance position 17 is "Expenses" in the layout and "Total Expenses" in
  narrative text. Same field.

Nothing in this module infers an outcome from a field name. Every enumeration
below is transcribed from the "VALID VALUES" column of the official
documentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

SCHEMA_VERSION: Final[str] = "freddie-llds-2026-08-10"
VERIFIED_AGAINST: Final[dict[str, str]] = {
    "file_layout.xlsx": "https://www.freddiemac.com/fmac-resources/research/pdf/file_layout.xlsx",
    "user_guide.pdf": "https://www.freddiemac.com/fmac-resources/research/pdf/user_guide.pdf",
    "verified_on": "2026-08-10",
}

DType = Literal["str", "int", "float", "yyyymm", "rate", "money"]


@dataclass(frozen=True, slots=True)
class Field:
    """One column of an official Freddie Mac flat file."""

    position: int
    """1-indexed column position in the pipe-delimited file."""
    name: str
    """Our snake_case name."""
    official: str
    """The official ATTRIBUTE NAME, verbatim."""
    dtype: DType
    na_values: tuple[str, ...] = ()
    """Sentinel strings that the official docs define as 'Not Available'."""
    note: str = ""


# ---------------------------------------------------------------------------
# Origination Data File -- 32 fields
# ---------------------------------------------------------------------------

ORIGINATION_FIELDS: Final[tuple[Field, ...]] = (
    Field(
        1, "credit_score", "Credit Score", "int", ("9999",), "301-850 valid; 9999 = Not Available."
    ),
    Field(2, "first_payment_date", "First Payment Date", "yyyymm"),
    Field(
        3,
        "first_time_homebuyer_flag",
        "First Time Homebuyer Flag",
        "str",
        ("9",),
        "Y / N / 9=Not Available. Not populated for refinance loans.",
    ),
    Field(4, "maturity_date", "Maturity Date", "yyyymm"),
    Field(
        5,
        "msa_code",
        "Metropolitan Statistical Area (MSA) Or Metropolitan Division",
        "str",
        ("", "00000"),
        "Null = not in an MSA/Metro Division, or unknown. NOT updated for "
        "changing MSA definitions -- a versioned crosswalk is required.",
    ),
    Field(
        6,
        "mi_percent",
        "Mortgage Insurance Percentage (MI %)",
        "int",
        ("999",),
        "1-55 valid; 0 = No MI; 999 = Not Available.",
    ),
    Field(7, "num_units", "Number of Units", "int", ("99",), "1-4 valid; 99 = Not Available."),
    Field(
        8,
        "occupancy_status",
        "Occupancy Status",
        "str",
        ("9",),
        "P=Primary Residence, I=Investment Property, S=Second Home, 9=Not Available.",
    ),
    Field(
        9,
        "orig_cltv",
        "Original Combined Loan-to-Value (CLTV)",
        "int",
        ("999",),
        "Ranges differ pre/post 2018Q2; 999 = Not Available. Set to NA if CLTV<LTV.",
    ),
    Field(
        10,
        "orig_dti",
        "Original Debt-to-Income (DTI) Ratio",
        "int",
        ("999",),
        "0<DTI<=65 valid; >65 and all HARP loans = 999 Not Available.",
    ),
    Field(11, "orig_upb", "Original UPB", "money", (), "Rounded to the nearest $1,000."),
    Field(
        12,
        "orig_ltv",
        "Original Loan-to-Value (LTV)",
        "int",
        ("999",),
        "6-105 (<=2018Q1) / 1-998 (>=2018Q2); 999 = Not Available.",
    ),
    Field(
        13,
        "orig_interest_rate",
        "Original Interest Rate",
        "rate",
        (),
        "Percent, literal decimal, e.g. 6.875.",
    ),
    Field(
        14,
        "channel",
        "Channel",
        "str",
        ("9",),
        "R=Retail, B=Broker, C=Correspondent, T=TPO Not Specified, 9=Not Available.",
    ),
    Field(15, "ppm_flag", "Prepayment Penalty Mortgage (PPM) Flag", "str", (), "Y=PPM, N=Not PPM."),
    Field(
        16,
        "amortization_type",
        "Amortization Type (Formerly Product Type)",
        "str",
        (),
        "FRM=Fixed Rate Mortgage, ARM=Adjustable Rate Mortgage.",
    ),
    Field(17, "property_state", "Property State", "str", ()),
    Field(
        18,
        "property_type",
        "Property Type",
        "str",
        ("99",),
        "CO=Condo, PU=PUD, MH=Manufactured Housing, SF=Single-Family, CP=Co-op, 99=Not Available.",
    ),
    Field(
        19,
        "postal_code",
        "Postal Code",
        "str",
        ("", "00000"),
        "First three digits of the ZIP followed by '00'. NOT a full ZIP; "
        "cannot be used to identify a property.",
    ),
    Field(
        20,
        "loan_seq_no",
        "Loan Sequence Number",
        "str",
        (),
        "PYYQnXXXXXXX. P: F=FRM, A=ARM. YYQn = origination year and quarter.",
    ),
    Field(
        21,
        "loan_purpose",
        "Loan Purpose",
        "str",
        ("9",),
        "P=Purchase, C=Refinance-Cash Out, N=Refinance-No Cash Out, "
        "R=Refinance-Not Specified, 9=Not Available.",
    ),
    Field(
        22,
        "orig_loan_term",
        "Original Loan Term",
        "int",
        (),
        "(Maturity Date - First Payment Date) + 1, in months.",
    ),
    Field(
        23,
        "num_borrowers",
        "Number of Borrowers",
        "int",
        ("99",),
        "01/02 semantics differ pre/post 2018Q2; 99 = Not Available.",
    ),
    Field(
        24,
        "seller_name",
        "Seller Name",
        "str",
        (),
        "'Other Sellers' where the seller is <1% of quarterly original UPB.",
    ),
    Field(
        25,
        "servicer_name",
        "Servicer Name",
        "str",
        (),
        "'Other Servicers' where the servicer is <1% of quarterly original UPB.",
    ),
    Field(
        26,
        "super_conforming_flag",
        "Super Conforming Flag",
        "str",
        ("",),
        "Y=Yes; blank = Not Super Conforming.",
    ),
    Field(
        27,
        "pre_relief_refi_loan_seq_no",
        "Pre-Relief Refinance Loan Sequence Number",
        "str",
        ("",),
        "Populated ONLY for Relief Refinance loans. Identifies a HARP/Relief "
        "chain, NOT ordinary refinancing. See DECISION_LOG D005.",
    ),
    Field(
        28,
        "special_eligibility_program",
        "Special Eligibility Program",
        "str",
        ("9",),
        "H=Home Possible, F=HFA Advantage, R=Refi Possible, 9=Not Available.",
    ),
    Field(
        29,
        "relief_refi_indicator",
        "Relief Refinance Indicator",
        "str",
        ("",),
        "Y=Relief Refinance loan; blank otherwise. Relief Refi with orig LTV>80 are HARP loans.",
    ),
    Field(
        30,
        "property_valuation_method",
        "Property Valuation Method",
        "int",
        ("7",),
        "1=Appraisal Waiver (ACE), 2=Appraisal, 3=Other, 4=ACE+PDR, "
        "7=Not Available. Populated for originations from 2017-01-01.",
    ),
    Field(31, "interest_only_indicator", "Interest Only (I/O) Indicator", "str", (), "Y/N."),
    Field(
        32,
        "mi_cancellation_indicator",
        "Mortgage Insurance Cancellation Indicator",
        "str",
        ("7", ""),
        "Cancelled after Freddie Mac purchase; 'Not Applicable' if no MI at purchase.",
    ),
)

# ---------------------------------------------------------------------------
# Monthly Performance Data File -- 32 fields
# ---------------------------------------------------------------------------

PERFORMANCE_FIELDS: Final[tuple[Field, ...]] = (
    Field(1, "loan_seq_no", "Loan Sequence Number", "str", ()),
    Field(
        2,
        "monthly_reporting_period",
        "Monthly Reporting Period",
        "yyyymm",
        (),
        "Combines the current month's accounting cycle for performing loans with "
        "the PREVIOUS calendar month's default reporting for non-performing loans. "
        "Accounting cycle was 16th-to-15th through 2019-04 and calendar-month from "
        "2019-05.",
    ),
    Field(3, "current_upb", "Current Actual UPB", "money"),
    Field(
        4,
        "delinquency_status",
        "Current Loan Delinquency Status",
        "str",
        ("XX", ""),
        "0=current, 1=30-59d, 2=60-89d, 3=90-119d, ... RA=REO Acquisition.",
    ),
    Field(
        5,
        "loan_age",
        "Loan Age",
        "int",
        (),
        "Scheduled payments since origination (or since MODIFICATION first payment "
        "date for modified loans -- loan age RESETS on modification, but not on a "
        "payment deferral).",
    ),
    Field(6, "remaining_months_to_maturity", "Remaining Months to Legal Maturity", "int"),
    Field(7, "defect_settlement_date", "Defect Settlement Date", "yyyymm", ("",)),
    Field(
        8,
        "modification_flag",
        "Modification Flag",
        "str",
        ("",),
        "Y=Current Period Modification, P=Prior Period Modification, blank=Not Modified.",
    ),
    Field(
        9,
        "zero_balance_code",
        "Zero Balance Code",
        "str",
        ("",),
        "See ZERO_BALANCE_CODES. Set at most once per loan, at the highest-priority "
        "termination event.",
    ),
    Field(
        10,
        "zero_balance_effective_date",
        "Zero Balance Effective Date",
        "yyyymm",
        ("",),
        "The period in which the triggering event took place.",
    ),
    Field(
        11,
        "current_interest_rate",
        "Current Interest Rate",
        "rate",
        (),
        "Reflects loan modifications.",
    ),
    Field(
        12,
        "current_deferred_upb",
        "Current Deferred UPB",
        "money",
        (),
        "user_guide.pdf calls this 'Current Non-Interest Bearing UPB'.",
    ),
    Field(13, "ddlpi", "Due Date of Last Paid Installment (DDLPI)", "yyyymm", ("",)),
    Field(14, "mi_recoveries", "MI Recoveries", "money", ("",)),
    Field(
        15,
        "net_sales_proceeds",
        "Net Sales Proceeds",
        "str",
        ("",),
        "Alpha-numeric; may contain 'U' (unknown) or 'C' (covered). "
        "Populated for ZB 02, 03, 09, 15.",
    ),
    Field(16, "non_mi_recoveries", "Non MI Recoveries", "money", ("",)),
    Field(17, "expenses", "Expenses", "money", ("",)),
    Field(18, "legal_costs", "Legal Costs", "money", ("",)),
    Field(19, "maintenance_costs", "Maintenance and Preservation Costs", "money", ("",)),
    Field(20, "taxes_and_insurance", "Taxes and Insurance", "money", ("",)),
    Field(21, "misc_expenses", "Miscellaneous Expenses", "money", ("",)),
    Field(22, "actual_loss", "Actual Loss Calculation", "money", ("",)),
    Field(23, "cumulative_modification_cost", "Modification Cost", "money", ("",)),
    Field(24, "step_modification_flag", "Step Modification Flag", "str", ("",)),
    Field(25, "deferred_payment_plan", "Deferred Payment Plan", "str", ("",)),
    Field(
        26,
        "reported_eltv",
        "Estimated Loan-to-Value (ELTV)",
        "int",
        ("", "999"),
        "Freddie Mac's own estimate; populated only for a subset of loan-periods. "
        "See DECISION_LOG D010.",
    ),
    Field(27, "zero_balance_removal_upb", "Zero Balance Removal UPB", "money", ("",)),
    Field(28, "delinquent_accrued_interest", "Delinquent Accrued Interest", "money", ("",)),
    Field(
        29,
        "delinquency_due_to_disaster",
        "Delinquency Due to Disaster",
        "str",
        ("",),
        "Y = the delinquency is disaster-related.",
    ),
    Field(
        30,
        "borrower_assistance_status",
        "Borrower Assistance Status Code",
        "str",
        ("",),
        "F=Forbearance, R=Repayment, T=Trial Period, blank=Not in assistance.",
    ),
    Field(31, "current_month_modification_cost", "Current Month Modification Cost", "money", ("",)),
    Field(32, "interest_bearing_upb", "Interest Bearing UPB", "money", ("",)),
)

# ---------------------------------------------------------------------------
# Zero Balance Codes -- official termination-event priority table
# user_guide.pdf, "Zero Balance Codes": priority 1 = highest.
# ---------------------------------------------------------------------------

EventClass = Literal["prepayment", "credit_event", "admin_removal"]


@dataclass(frozen=True, slots=True)
class ZeroBalanceCode:
    code: str
    official_label: str
    priority: int
    """1 = highest priority. If two termination events occur in the same reporting
    period, the higher-ranking one is the one reported."""
    event_class: EventClass
    censoring: bool
    """True => we treat the loan as right-censored at this date rather than as
    having experienced a behavioural exit."""
    rationale: str


ZERO_BALANCE_CODES: Final[dict[str, ZeroBalanceCode]] = {
    "15": ZeroBalanceCode(
        "15",
        "Whole Loan Sale",
        1,
        "admin_removal",
        True,
        "Freddie Mac portfolio action. Not a borrower decision. Censored.",
    ),
    "16": ZeroBalanceCode(
        "16",
        "Reperforming loan securitizations",
        2,
        "admin_removal",
        True,
        "Freddie Mac portfolio action (RPL securitization). Not a borrower decision. Censored.",
    ),
    "09": ZeroBalanceCode(
        "09",
        "REO Disposition",
        3,
        "credit_event",
        False,
        "Terminal credit outcome following foreclosure.",
    ),
    "96": ZeroBalanceCode(
        "96",
        "Defect prior to other termination event",
        4,
        "admin_removal",
        True,
        "Repurchase / indemnification / make-whole arising from a representation "
        "and warranty defect. Not a borrower decision. Censored.",
    ),
    "03": ZeroBalanceCode(
        "03",
        "Short Sale or Charge Off",
        5,
        "credit_event",
        False,
        "Terminal credit outcome.",
    ),
    "02": ZeroBalanceCode(
        "02",
        "Third Party Sale",
        6,
        "credit_event",
        False,
        "Sale to a third party at foreclosure auction. A CREDIT outcome, not a "
        "voluntary household move.",
    ),
    "01": ZeroBalanceCode(
        "01",
        "Prepaid or Matured (Voluntary Payoff)",
        7,
        "prepayment",
        False,
        "CONFLATES voluntary payoff and scheduled maturity, and does NOT "
        "distinguish refinance from sale-related payoff. This is the single most "
        "important limitation of the loan-level design.",
    ),
}

CENSORING_ZB_CODES: Final[frozenset[str]] = frozenset(
    c for c, z in ZERO_BALANCE_CODES.items() if z.censoring
)
PREPAYMENT_ZB_CODES: Final[frozenset[str]] = frozenset(
    c for c, z in ZERO_BALANCE_CODES.items() if z.event_class == "prepayment"
)
CREDIT_EVENT_ZB_CODES: Final[frozenset[str]] = frozenset(
    c for c, z in ZERO_BALANCE_CODES.items() if z.event_class == "credit_event"
)

# Enumerations we rely on downstream, transcribed from the user guide.
LOAN_PURPOSE: Final[dict[str, str]] = {
    "P": "Purchase",
    "C": "Refinance - Cash Out",
    "N": "Refinance - No Cash Out",
    "R": "Refinance - Not Specified",
    "9": "Not Available",
}
OCCUPANCY_STATUS: Final[dict[str, str]] = {
    "P": "Primary Residence",
    "I": "Investment Property",
    "S": "Second Home",
    "9": "Not Available",
}
PROPERTY_TYPE: Final[dict[str, str]] = {
    "SF": "Single-Family",
    "CO": "Condo",
    "PU": "PUD",
    "MH": "Manufactured Housing",
    "CP": "Co-op",
    "99": "Not Available",
}
AMORTIZATION_TYPE: Final[dict[str, str]] = {
    "FRM": "Fixed Rate Mortgage",
    "ARM": "Adjustable Rate Mortgage",
}
CHANNEL: Final[dict[str, str]] = {
    "R": "Retail",
    "B": "Broker",
    "C": "Correspondent",
    "T": "TPO Not Specified",
    "9": "Not Available",
}

# Loan Sequence Number product prefix -> amortization type.
LOAN_SEQ_PRODUCT_PREFIX: Final[dict[str, str]] = {"F": "FRM", "A": "ARM"}

ORIGINATION_COLUMNS: Final[tuple[str, ...]] = tuple(f.name for f in ORIGINATION_FIELDS)
PERFORMANCE_COLUMNS: Final[tuple[str, ...]] = tuple(f.name for f in PERFORMANCE_FIELDS)

_ORIG_BY_NAME: Final[dict[str, Field]] = {f.name: f for f in ORIGINATION_FIELDS}
_PERF_BY_NAME: Final[dict[str, Field]] = {f.name: f for f in PERFORMANCE_FIELDS}


def origination_field(name: str) -> Field:
    """Look up an origination field by our snake_case name."""
    return _ORIG_BY_NAME[name]


def performance_field(name: str) -> Field:
    """Look up a performance field by our snake_case name."""
    return _PERF_BY_NAME[name]


def classify_zero_balance(code: str | None) -> tuple[str, bool]:
    """Map a Zero Balance Code to ``(event_class, is_censoring)``.

    An unset or blank code means the loan was still active as of the performance
    cutoff -- right censoring, not an event.

    An *unknown* code is deliberately treated as ``admin_removal`` + censoring
    rather than guessed at, because guessing an outcome from an undocumented code
    is exactly what ``AGENTS.md`` forbids.
    """
    if code is None or code.strip() == "":
        return ("censored_active", True)
    zb = ZERO_BALANCE_CODES.get(code.strip().zfill(2))
    if zb is None:
        return ("unknown_zb_code", True)
    return (zb.event_class, zb.censoring)


def layout_spec() -> dict[str, Any]:
    """The full layout as a plain dict, for serialising to YAML.

    This is the single source of truth: ``data/reference/freddie_llds_layout.yaml``
    is generated from it by ``lockin emit-layout``, and a test fails if the two
    ever disagree. Committing the YAML makes the field map reviewable without
    reading Python; generating it means the two cannot drift.
    """

    def fields(fs: tuple[Field, ...]) -> list[dict[str, Any]]:
        return [
            {
                "position": f.position,
                "name": f.name,
                "official_name": f.official,
                "dtype": f.dtype,
                "na_values": list(f.na_values),
                "note": f.note,
            }
            for f in fs
        ]

    return {
        "schema_version": SCHEMA_VERSION,
        "verified_against": dict(VERIFIED_AGAINST),
        "provenance_note": (
            "GENERATED from src/lockin/schemas/freddie.py by `lockin emit-layout`. Do "
            "not hand-edit: tests/test_ingest.py::test_layout_yaml_matches_schema fails "
            "if this file and the module disagree. The field definitions themselves "
            "were transcribed from Freddie Mac's PUBLIC file_layout.xlsx and "
            "user_guide.pdf, which are served without registration. The DATA are not "
            "redistributed here."
        ),
        "file_format": {
            "delimiter": "|",
            "header": False,
            "encoding": "latin-1",
            "origination_field_count": len(ORIGINATION_FIELDS),
            "performance_field_count": len(PERFORMANCE_FIELDS),
        },
        "origination_fields": fields(ORIGINATION_FIELDS),
        "performance_fields": fields(PERFORMANCE_FIELDS),
        "zero_balance_codes": [
            {
                "code": z.code,
                "official_label": z.official_label,
                "priority": z.priority,
                "event_class": z.event_class,
                "treated_as_censoring": z.censoring,
                "rationale": z.rationale,
            }
            for z in sorted(ZERO_BALANCE_CODES.values(), key=lambda x: x.priority)
        ],
        "enumerations": {
            "loan_purpose": dict(LOAN_PURPOSE),
            "occupancy_status": dict(OCCUPANCY_STATUS),
            "property_type": dict(PROPERTY_TYPE),
            "amortization_type": dict(AMORTIZATION_TYPE),
            "channel": dict(CHANNEL),
        },
    }


def assert_layout_verified() -> None:
    """Guard used by ``lockin verify-schema``."""
    if len(ORIGINATION_FIELDS) != 32:
        raise AssertionError(f"expected 32 origination fields, have {len(ORIGINATION_FIELDS)}")
    if len(PERFORMANCE_FIELDS) != 32:
        raise AssertionError(f"expected 32 performance fields, have {len(PERFORMANCE_FIELDS)}")
    for i, f in enumerate(ORIGINATION_FIELDS, start=1):
        if f.position != i:
            raise AssertionError(
                f"origination field {f.name} at position {f.position}, expected {i}"
            )
    for i, f in enumerate(PERFORMANCE_FIELDS, start=1):
        if f.position != i:
            raise AssertionError(
                f"performance field {f.name} at position {f.position}, expected {i}"
            )
    prios = sorted(z.priority for z in ZERO_BALANCE_CODES.values())
    if prios != list(range(1, len(ZERO_BALANCE_CODES) + 1)):
        raise AssertionError(f"ZB priorities are not a 1..n permutation: {prios}")
