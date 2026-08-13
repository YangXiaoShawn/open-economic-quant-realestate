"""Data-governance and vocabulary guards.

These tests are the mechanical enforcement of the rules in
``data/LICENSE_AND_REDISTRIBUTION.md`` and ``AGENTS.md`` §1. They are cheap and
they catch the two failure modes that would most damage the project's credibility:
committing restricted data, and calling a prepayment a move.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from lockin.config import REPO_ROOT, load_config

BANNED_PATH_PREFIXES = (
    "data/raw/",
    "data/cache/",
    "data/interim/",
    "data/processed/",
    "outputs/",
)
BANNED_SUFFIXES = (".parquet", ".zip", ".gz", ".txt.gz")
MAX_TRACKED_BYTES = 5_000_000


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
    )
    if out.returncode != 0:
        pytest.skip("not a git repository")
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def test_no_restricted_paths_are_tracked() -> None:
    offenders = [f for f in _tracked_files() if f.startswith(BANNED_PATH_PREFIXES)]
    assert offenders == [], f"restricted-path files are tracked by git: {offenders}"


def test_no_bulk_data_files_are_tracked() -> None:
    offenders = [f for f in _tracked_files() if f.endswith(BANNED_SUFFIXES)]
    assert offenders == [], f"bulk-data files are tracked by git: {offenders}"


def test_no_tracked_file_is_oversized() -> None:
    offenders = []
    for f in _tracked_files():
        p = REPO_ROOT / f
        if p.is_file() and p.stat().st_size > MAX_TRACKED_BYTES:
            offenders.append((f, p.stat().st_size))
    assert offenders == [], f"tracked files exceed {MAX_TRACKED_BYTES} bytes: {offenders}"


def test_gitignore_blocks_the_restricted_tree() -> None:
    text = (REPO_ROOT / ".gitignore").read_text()
    for pat in ("data/raw/", "data/interim/", "data/processed/", "outputs/", "*.parquet"):
        assert pat in text, f".gitignore is missing {pat!r}"


# ---------------------------------------------------------------------------
# Vocabulary: a prepayment is not a move.
# ---------------------------------------------------------------------------

#: Phrases that would assert we observe mobility or a sale at the loan level.
FORBIDDEN_PATTERNS = (
    r"\bhome[_ ]sale[s]?\s+(?:event|observed|indicator)\b",
    r"\bmove[_ ]event\b",
    r"\bhousehold[_ ]move[s]?\s+(?:event|observed|indicator)\b",
    r"\bprepayment\s+(?:is|means|=)\s+(?:a\s+)?(?:sale|move)\b",
    r"\bprepayments?\s+as\s+(?:sales|moves|mobility)\b",
    r"\btreat\s+prepayment\s+as\s+(?:a\s+)?(?:sale|move)\b",
)

#: Files that legitimately discuss the forbidden phrases in order to forbid them.
#:
#: A regex cannot tell "a prepayment is a sale" from "never say a prepayment is a
#: sale" -- negation and quotation are beyond its reach. So the guard is deliberately
#: over-sensitive and this allowlist carries the files whose *purpose* includes stating
#: the rule. Adding a file here is a real decision: it means nobody will catch a
#: genuine slip in that file automatically. Every entry below is a document about the
#: vocabulary rule, a schema/event module that must name what it refuses to emit, or a
#: renderer that emits the warning text.
ALLOWLIST = {
    "AGENTS.md",
    "README.md",
    "tests/test_governance.py",
    "tests/test_events.py",
    "docs/RESEARCH_DESIGN.md",
    "docs/DECISION_LOG.md",
    "docs/IDENTIFICATION_STRATEGY.md",
    "docs/PROJECT_PLAN.md",
    "src/lockin/events.py",
    "src/lockin/schemas/freddie.py",
    "src/lockin/adapters/freddie_llds.py",
    "src/lockin/reporting/render.py",
    "src/lockin/simulate/scenarios.py",
    "src/lockin/benchmark.py",
    "data/DATA_DICTIONARY.md",
    # Portfolio documents whose "what not to say" sections quote the rule verbatim.
    "portfolio/interview_story.md",
    "portfolio/ten_minute_presentation_outline.md",
    "portfolio/thirty_minute_research_presentation.md",
    "portfolio/resume_bullets.md",
}


def test_no_mobility_language_in_code_or_reports() -> None:
    """No file may assert that we observe a sale or a move, except the files whose
    job is to explain that we do not."""
    offenders: list[tuple[str, str]] = []
    for f in _tracked_files():
        if not f.endswith((".py", ".md", ".yaml", ".yml")):
            continue
        if f in ALLOWLIST:
            continue
        p = REPO_ROOT / f
        if not p.is_file():
            continue
        text = p.read_text(errors="replace")
        for pat in FORBIDDEN_PATTERNS:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                offenders.append((f, m.group(0)))
    assert offenders == [], (
        f"files assert that a prepayment is a sale or a move (AGENTS.md section 1): {offenders}"
    )


def test_generated_reports_carry_the_generated_header() -> None:
    """A report without the header could be mistaken for a hand-written document."""
    cfg = load_config("configs/sample.yaml")
    reports = sorted(cfg.path("reports").glob("*.md")) if cfg.path("reports").exists() else []
    if not reports:
        pytest.skip("no reports generated yet; run `make report`")
    for p in reports:
        text = p.read_text()
        assert "GENERATED" in text.split("\n\n")[0] or "GENERATED" in text[:600], (
            f"{p.name} lacks a GENERATED header"
        )


def test_synthetic_runs_render_the_banner() -> None:
    """A report built from synthetic artifacts must carry the banner. There is no
    flag to disable this."""
    cfg = load_config("configs/sample.yaml")
    reports = sorted(cfg.path("reports").glob("*.md")) if cfg.path("reports").exists() else []
    if not reports:
        pytest.skip("no reports generated yet; run `make report`")
    # `reports/` is a shared path: the markdown on disk belongs to whichever profile
    # last rendered, which need not be the config this test loaded. Ask the directory
    # what produced it rather than assuming.
    from lockin import dataset_stamp

    stamp = dataset_stamp.read(cfg.path("reports"))
    produced_by = stamp.get("data_class") if stamp else cfg.data_class
    if produced_by != "SYNTHETIC":
        pytest.skip(f"reports on disk were produced by a {produced_by} run")
    # Reports that consume no loan-level artifact legitimately have no banner; at
    # least the loan-level ones must.
    must_have = [
        "loan_hazard_analysis.md",
        "technical_report.md",
        "executive_housing_policy_memo.md",
    ]
    for name in must_have:
        p = cfg.path("reports", name)
        if not p.exists():
            continue
        assert "SYNTHETIC DATA" in p.read_text(), f"{name} is missing the synthetic banner"


def test_render_refuses_a_missing_banner() -> None:
    """The guard must actually fire."""
    from lockin.reporting.render import ReportContext, _assert_banner

    cfg = load_config("configs/sample.yaml")
    ctx = ReportContext(cfg)
    ctx.any_synthetic = True
    with pytest.raises(RuntimeError, match="synthetic banner"):
        _assert_banner(ctx, "a report body with no banner")
    _assert_banner(ctx, "... SYNTHETIC DATA ...")  # must not raise


def test_licence_policy_documents_every_source() -> None:
    text = (REPO_ROOT / "data" / "LICENSE_AND_REDISTRIBUTION.md").read_text()
    for source in (
        "Freddie Mac Single-Family Loan-Level Dataset",
        "Primary Mortgage Market Survey",
        "FHFA House Price Index",
        "HMDA",
        "Building Permits Survey",
    ):
        assert source in text, f"licence policy does not mention {source}"
    assert "RESTRICTED" in text
