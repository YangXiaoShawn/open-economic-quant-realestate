"""Validator behaviour that only the real dataset exposed.

Two defects are covered. Both were invisible on fixtures and both would mislead rather
than merely inconvenience: a validation gate that never finishes is a gate nobody runs,
and a gate that always fires on real data is a gate nobody believes.
"""

from __future__ import annotations

from pathlib import Path

from lockin.ingest.origination import DOMAIN_VIOLATION_HARD_SHARE, _domain_problem
from lockin.manifest import _is_data_file


def test_a_handful_of_bad_records_in_millions_is_soft():
    """35 impossible loan terms out of 20M is source noise, not a parsing fault."""
    msg = _domain_problem("orig_loan_term outside (0, 480]", 35, 20_199_214, "[481, 544]")
    assert msg.startswith("SOFT:")
    assert "35" in msg and "20,199,214" in msg


def test_a_systematic_share_is_hard():
    """The same check must still stop a run when a layout shift breaks a whole column."""
    msg = _domain_problem("orig_loan_term outside (0, 480]", 2_000_000, 20_199_214, "[0]")
    assert msg.startswith("HARD:")
    assert "layout shift" in msg


def test_severity_threshold_is_crossed_where_documented():
    n = 1_000_000
    just_under = int(DOMAIN_VIOLATION_HARD_SHARE * n) - 1
    just_over = int(DOMAIN_VIOLATION_HARD_SHARE * n) + 10
    assert _domain_problem("x", just_under, n, "[]").startswith("SOFT:")
    assert _domain_problem("x", just_over, n, "[]").startswith("HARD:")


def test_the_observed_values_are_always_reported():
    """A count alone cannot be judged; the values are what tell you if it is a parse bug."""
    for n_bad in (1, 5_000_000):
        assert "[481, 544]" in _domain_problem("term", n_bad, 20_000_000, "[481, 544]")


def test_profile_stamp_is_excluded_from_the_directory_checksum(tmp_path: Path):
    """Writing the stamp must not invalidate the data it describes.

    It did: adding .lockin_profile.json to the interim directories made both dataset
    checksums mismatch, which validate-data reported as two HARD errors about data that
    had not changed at all.
    """
    (tmp_path / "part-0.parquet").write_bytes(b"data")
    assert _is_data_file(tmp_path / "part-0.parquet")
    assert not _is_data_file(tmp_path / ".lockin_profile.json")
    assert not _is_data_file(tmp_path / "_dataset.manifest.json")
