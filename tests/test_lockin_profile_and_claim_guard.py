"""Verify Freddie Mac 40-cohort lock-in profile and pre-trend auto-demotion claim guard."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE_SRC = ROOT.parent / "packages" / "oeq-core" / "src"
if CORE_SRC.exists() and str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from oeq_core import ClaimBoundary, EvidenceGrade, auto_demote_claim, validate_claim_boundary  # noqa: E402


class TestRealEstateLockinProfileAndClaimGuard(unittest.TestCase):
    def test_lockin_40_cohorts_and_pretrend_demotion(self) -> None:
        profile = json.loads((ROOT / "reports" / ".lockin_profile.json").read_text(encoding="utf-8"))
        self.assertEqual(len(profile["cohorts"]), 40)
        self.assertEqual(profile["cohorts"][0], "2013Q1")
        self.assertEqual(profile["cohorts"][-1], "2022Q4")
        self.assertEqual(profile["performance_start"], "2021-01")
        self.assertEqual(profile["performance_end"], "2024-12")

        # Event-study pre-trend p = 0.0046 requires automatic demotion to ASSOCIATIONAL_ONLY
        raw_boundary = ClaimBoundary(
            project_slug="realestate",
            grade=EvidenceGrade.CAUSAL_IDENTIFIED,
            allows_causal_language=True,
            allows_live_profitability_claim=False,
            passed_pretrend_or_placebo=False,
        )
        demoted = auto_demote_claim(raw_boundary)
        self.assertEqual(demoted.grade, EvidenceGrade.ASSOCIATIONAL_ONLY)
        self.assertFalse(demoted.allows_causal_language)
        self.assertEqual(validate_claim_boundary(demoted), [])


if __name__ == "__main__":
    unittest.main()
