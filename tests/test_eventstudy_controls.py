"""Control handling in the event study.

Three failure modes are covered, all of the same family: a specification that *looks*
like it controls for something but does not, producing an artifact whose stated controls
overstate what was actually held fixed.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from lockin.panel.eventstudy import (
    _circular_trend_controls,
    _demote_degenerate_controls,
    did_two_period,
    event_study,
)


def _panel(n_geo: int = 20, years=(2019, 2020, 2021, 2022, 2023), seed: int = 7) -> pl.DataFrame:
    """Panel where the outcome depends on a time-invariant characteristic x post."""
    rng = np.random.default_rng(seed)
    geos = [f"G{i:02d}" for i in range(n_geo)]
    exposure = {g: float(rng.normal()) for g in geos}
    fixed = {g: float(rng.normal()) for g in geos}
    rows = []
    for g in geos:
        for y in years:
            post = 1.0 if y >= 2022 else 0.0
            # The ONLY post-shock driver is the fixed characteristic, not exposure.
            y_val = fixed[g] * post * 2.0 + exposure[g] * 0.0 + rng.normal(scale=0.05)
            rows.append(
                {
                    "geography": g,
                    "year": y,
                    "outcome": y_val,
                    "exposure": exposure[g],
                    "fixed_char": fixed[g],
                    "varying": float(rng.normal()),
                }
            )
    return pl.DataFrame(rows)


def test_time_invariant_level_control_is_demoted_not_silently_absorbed():
    df = _panel()
    controls = ["fixed_char", "varying"]
    trend: list[str] = []
    moved = _demote_degenerate_controls(df, controls, trend)
    assert moved == ["fixed_char"]
    assert controls == ["varying"]
    assert trend == ["fixed_char"]


def test_time_varying_control_is_left_alone():
    df = _panel()
    controls = ["varying"]
    trend: list[str] = []
    assert _demote_degenerate_controls(df, controls, trend) == []
    assert controls == ["varying"]
    assert trend == []


def test_event_study_reports_the_demotion():
    res = event_study(_panel(), "outcome", "exposure", "year", 2021, controls=["fixed_char"])
    assert res["status"] == "ok"
    assert res["degenerate_controls"] == ["fixed_char"]
    assert "fixed_char" in res["trend_controls"]
    assert "fixed_char" not in res["controls"]


def test_trend_control_actually_absorbs_a_confounded_post_shock_jump():
    """A level control cannot remove this confound; a trend control can.

    The panel is built so the post-shock movement is driven entirely by a fixed
    characteristic. Controlling for it as a *trend* must shrink the residual variance
    that any spurious exposure effect could load on.
    """
    df = _panel()
    as_level = did_two_period(df, "outcome", "exposure", "year", 2022, controls=[])
    as_trend = did_two_period(
        df, "outcome", "exposure", "year", 2022, trend_controls=["fixed_char"]
    )
    assert as_level["status"] == as_trend["status"] == "ok"
    # The trend control explains the post-shock jump, so the exposure estimate is
    # measured far more precisely once it is included.
    assert as_trend["std_err"] < as_level["std_err"]


def test_did_and_event_study_agree_on_the_specification():
    """Both halves of one artifact must estimate the same thing.

    They previously did not: only ``event_study`` demoted degenerate controls, so the
    DiD kept a collinear level control the dynamic estimate had already moved.
    """
    df = _panel()
    kwargs = {"controls": ["fixed_char", "varying"], "trend_controls": []}
    es = event_study(
        df, "outcome", "exposure", "year", 2021, **{k: list(v) for k, v in kwargs.items()}
    )
    did = did_two_period(
        df, "outcome", "exposure", "year", 2022, **{k: list(v) for k, v in kwargs.items()}
    )
    assert es["controls"] == did["controls"]
    assert es["trend_controls"] == did["trend_controls"]
    assert es["degenerate_controls"] == did["degenerate_controls"]


def test_circularity_detected_when_control_shares_the_outcome_series():
    """Pre-period HPI growth as a trend control for an HPI-growth outcome."""
    assert _circular_trend_controls("hpi_growth", ["pre_hpi_growth_2019_2021"]) == [
        "pre_hpi_growth_2019_2021"
    ]


def test_no_circularity_across_different_published_series():
    assert _circular_trend_controls("log_purchase_originations", ["pre_hpi_growth_2019_2021"]) == []
    assert _circular_trend_controls("hpi_growth", ["teleworkable_share"]) == []


def test_unknown_outcome_is_not_assumed_circular():
    assert _circular_trend_controls("some_new_outcome", ["pre_hpi_growth_2019_2021"]) == []
