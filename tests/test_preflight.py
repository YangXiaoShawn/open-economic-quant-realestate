"""Preflight checks for registered loan-level archives.

The point of preflight is to fail in seconds rather than after a multi-hour ingest,
so the tests here are about the *findings* it produces, not about throughput.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from lockin.config import load_config
from lockin.preflight import PROBE_LINES, registration_steps, run_preflight
from lockin.schemas.freddie import ORIGINATION_COLUMNS, PERFORMANCE_COLUMNS


def _cfg(tmp_path: Path):
    cfg = load_config("configs/sample.yaml")
    cfg.paths.raw = str(tmp_path / "raw")
    cfg.mortgage.cohorts = ["2021Q4"]
    return cfg


def _orig_line() -> str:
    f = dict.fromkeys(ORIGINATION_COLUMNS, "")
    f.update(
        {
            "loan_seq_no": "F21Q40000001",
            "orig_interest_rate": "3.125",
            "orig_upb": "250000",
            "property_state": "CA",
            "orig_loan_term": "360",
            "first_payment_date": "202202",
            "maturity_date": "205201",
        }
    )
    return "|".join(f[c] for c in ORIGINATION_COLUMNS)


def _perf_line(zb: str = "") -> str:
    f = dict.fromkeys(PERFORMANCE_COLUMNS, "")
    f.update(
        {
            "loan_seq_no": "F21Q40000001",
            "monthly_reporting_period": "202301",
            "current_upb": "240000.00",
            "loan_age": "12",
            "zero_balance_code": zb,
        }
    )
    return "|".join(f[c] for c in PERFORMANCE_COLUMNS)


def _archive(root: Path, cohort: str, orig: list[str], perf: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(root / f"historical_data_{cohort}.zip", "w") as zf:
        zf.writestr(f"historical_data_{cohort}.txt", "\n".join(orig) + "\n")
        zf.writestr(f"historical_data_time_{cohort}.txt", "\n".join(perf) + "\n")


def test_missing_directory_is_a_blocker(tmp_path: Path) -> None:
    """Preflight searches the same places the ingester does: data/raw/freddie AND
    data/raw itself, because people leave the download where the browser put it."""
    pf = run_preflight(_cfg(tmp_path))
    assert pf.n_blockers == 1
    msg = pf.findings[0].message
    assert "exist" in msg
    assert "raw" in msg


def test_empty_directory_is_a_blocker(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    (tmp_path / "raw" / "freddie").mkdir(parents=True)
    pf = run_preflight(cfg)
    assert pf.n_blockers == 1
    assert "No recognised Freddie Mac files" in pf.findings[0].message


def test_well_formed_archive_passes(tmp_path: Path) -> None:
    """The path the user actually hits after a correct download."""
    cfg = _cfg(tmp_path)
    _archive(
        tmp_path / "raw" / "freddie", "2021Q4", [_orig_line()] * 5, [_perf_line(), _perf_line("01")]
    )
    pf = run_preflight(cfg)
    assert pf.n_blockers == 0
    assert pf.cohorts_origination == ["2021Q4"]
    assert pf.cohorts_performance == ["2021Q4"]
    assert pf.zb_codes_seen == {"01": 1}
    assert all(f["modal_field_count"] == f["expected_field_count"] for f in pf.files)


def test_unknown_field_count_is_a_blocker(tmp_path: Path) -> None:
    """A field count matching NO verified variant must stop the run.

    Note what this does and does not assert. Freddie Mac ships more than one layout --
    the documented 32/32 and the observed 31/35 of the 2026 full set -- so a count that
    merely differs from the documented one is not by itself an error. A count matching
    neither is, because the position of the Zero Balance Code would then be a guess.
    """
    cfg = _cfg(tmp_path)
    truncated = "|".join(["x"] * 20)
    _archive(tmp_path / "raw" / "freddie", "2021Q4", [truncated] * 3, [_perf_line()])
    pf = run_preflight(cfg)
    assert pf.n_blockers >= 1
    assert any("matches no verified layout variant" in f.message for f in pf.findings)


def test_the_shipped_31_35_layout_is_not_a_blocker(tmp_path: Path) -> None:
    """The real full-set download must pass preflight, and say so loudly.

    The published documentation describes 32/32; the shipped files are 31/35. Blocking
    on that would reject the actual dataset, so the variant is accepted -- but because it
    rests on inference rather than documentation, preflight reports that explicitly.
    """
    cfg = _cfg(tmp_path)
    orig31 = "|".join(["x"] * 31)
    perf35 = _perf_line().split("|")[:32] + ["N", "SOME SERVICER", ""]
    _archive(tmp_path / "raw" / "freddie", "2021Q4", [orig31] * 3, ["|".join(perf35)])
    pf = run_preflight(cfg)
    assert pf.n_blockers == 0
    assert any("NOT described by any published" in f.message for f in pf.findings)
    assert all(f.get("layout_variant") == "observed_31_35" for f in pf.files)


def test_undocumented_zero_balance_code_is_a_blocker(tmp_path: Path) -> None:
    """The whole point: an unknown code would otherwise be silently censored,
    discarding real exits."""
    cfg = _cfg(tmp_path)
    _archive(
        tmp_path / "raw" / "freddie", "2021Q4", [_orig_line()], [_perf_line("77"), _perf_line("01")]
    )
    pf = run_preflight(cfg)
    assert pf.n_blockers >= 1
    msg = " ".join(f.message for f in pf.findings if f.level == "BLOCKER")
    assert "UNDOCUMENTED" in msg and "77" in msg
    assert "refuses to guess" in msg


def test_origination_without_performance_is_a_blocker(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    root = tmp_path / "raw" / "freddie"
    root.mkdir(parents=True)
    with zipfile.ZipFile(root / "historical_data_2021Q4.zip", "w") as zf:
        zf.writestr("historical_data_2021Q4.txt", _orig_line() + "\n")
    pf = run_preflight(cfg)
    assert pf.n_blockers >= 1
    assert any("NO performance file" in f.message for f in pf.findings)


def test_configured_cohort_absent_is_a_warning_not_a_blocker(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.mortgage.cohorts = ["2021Q4", "2020Q1"]
    _archive(tmp_path / "raw" / "freddie", "2021Q4", [_orig_line()], [_perf_line()])
    pf = run_preflight(cfg)
    assert pf.n_blockers == 0
    assert any("2020Q1" in f.message and f.level == "WARNING" for f in pf.findings)


def test_probe_reads_only_the_head(tmp_path: Path) -> None:
    """Preflight must not read a multi-GB member end to end."""
    cfg = _cfg(tmp_path)
    _archive(
        tmp_path / "raw" / "freddie",
        "2021Q4",
        [_orig_line()] * 5,
        [_perf_line()] * (PROBE_LINES * 4),
    )
    pf = run_preflight(cfg)
    perf = next(f for f in pf.files if f["kind"] == "performance")
    assert perf["lines_probed"] == PROBE_LINES


def test_synthetic_mode_is_flagged(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _archive(tmp_path / "raw" / "freddie", "2021Q4", [_orig_line()], [_perf_line()])
    pf = run_preflight(cfg)
    assert any("mortgage.mode is still 'synthetic'" in f.message for f in pf.findings)


def test_registration_steps_do_not_ask_for_credentials() -> None:
    steps = " ".join(registration_steps())
    assert "ACCEPT THE TERMS OF USE" in steps
    assert "will not bypass" in steps
    assert "does not want a copy of your credentials" in steps


@pytest.mark.parametrize("kind", ["origination", "performance"])
def test_expected_field_count_matches_verified_layout(tmp_path: Path, kind: str) -> None:
    cfg = _cfg(tmp_path)
    _archive(tmp_path / "raw" / "freddie", "2021Q4", [_orig_line()], [_perf_line()])
    pf = run_preflight(cfg)
    f = next(x for x in pf.files if x["kind"] == kind)
    assert f["expected_field_count"] == 32
