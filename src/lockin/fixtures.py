"""SYNTHETIC loan fixture generator.

Purpose: exercise every parser, join, event rule, and estimator when the
registered Freddie Mac dataset is unavailable. The fixtures are **schema-exact** --
the same 32 + 32 pipe-delimited fields, the same sentinel missing-value codes, the
same Zero Balance Code semantics -- so the code paths tested here are the same ones
that will run on real data.

**These are not data about the United States.** Every artifact produced from them
is stamped ``SYNTHETIC`` and every report carrying such a number renders a banner.
The data-generating process below is a *deliberately simple* behavioural model; any
estimated coefficient recovers that model's parameters, not a fact about American
homeowners. Do not quote a number from a synthetic run.

Design of the DGP (documented so nobody mistakes output for evidence):

* Loans are drawn per origination cohort with note rates centred on the **real**
  PMMS rate prevailing in that cohort's quarter, so the coupon distribution has a
  realistic shape across cohorts and the 2020-21 low-rate wave is present.
* State assignment uses fixed shares, and states differ in the *cohort mix* they
  draw from, which is what creates cross-state variation in predetermined lock-in
  exposure.
* Monthly prepayment hazard is a logistic function of the point-in-time refinance
  incentive plus loan-age duration dependence. The coefficient is set by
  ``PREPAY_RATE_GAP_COEF`` -- a **known** value that the hazard estimator should
  approximately recover. That recovery is a *software test*, not a finding.
* A small credit-event hazard and a small administrative-removal hazard create
  genuine competing risks and genuine censoring.
* Left truncation is generated explicitly: a random subset of loans has its first
  performance record delayed, mimicking the acquisition lag.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from lockin.amortization import remaining_balance
from lockin.config import Config
from lockin.manifest import write_manifest
from lockin.schemas.freddie import (
    ORIGINATION_COLUMNS,
    PERFORMANCE_COLUMNS,
    SCHEMA_VERSION,
)

# --- known DGP parameters (a synthetic run should approximately recover these) ---

#: Coefficient on the refinance incentive (percentage points) in the monthly
#: prepayment log-odds. Positive: a larger refi incentive raises prepayment.
PREPAY_RATE_GAP_COEF: float = 0.55
#: Baseline monthly prepayment log-odds at age 24 with zero incentive.
PREPAY_BASE_LOGIT: float = -4.6
#: Loan-age ramp (log-odds per month, up to a plateau).
PREPAY_AGE_RAMP: float = 0.012
PREPAY_AGE_PLATEAU: int = 36
#: Monthly credit-event hazard log-odds, plus sensitivity to current LTV.
CREDIT_BASE_LOGIT: float = -8.0
CREDIT_LTV_COEF: float = 0.020
#: Monthly administrative-removal (ZB 15/16/96) hazard -- pure censoring.
ADMIN_REMOVAL_MONTHLY_PROB: float = 0.0012
#: Monthly modification probability among delinquent loans.
MODIFICATION_MONTHLY_PROB: float = 0.004

#: Share of synthetic loans left outside any CBSA. Roughly a sixth of US mortgage
#: originations are in non-metropolitan areas, and a null MSA field must stay on the
#: exercised code path -- the MSA-level panel has to drop those loans, not impute them.
NON_METRO_SHARE: float = 0.16


def _fixture_msa_codes(cfg) -> dict[str, list[str]]:
    """Real, composition-stable metropolitan CBSA codes for each fixture state.

    Returns ``{}`` when the OMB crosswalk is not cached. Fixtures must not invent
    five-digit codes: a fabricated code would either fail to resolve (making the
    crosswalk look broken) or collide with a real metro (making a synthetic result
    look attributable to a real place).
    """
    from lockin.adapters import omb_cbsa

    try:
        cw = omb_cbsa.load_crosswalk(cfg)
        stab = omb_cbsa.load_stability(cfg)
    except (FileNotFoundError, OSError):
        return {}

    import polars as pl

    usable = set(
        stab.filter(
            (pl.col("code_kind") == "cbsa") & pl.col("verdict").is_in(["stable", "renamed_only"])
        )["area_code"].to_list()
    )
    fips_to_state = {s[1]: s[0] for s in STATE_MIX}
    out: dict[str, list[str]] = {}
    sub = cw.filter(
        (pl.col("vintage") == omb_cbsa.DEFAULT_ANALYSIS_VINTAGE)
        & (pl.col("code_kind") == "cbsa")
        & (pl.col("cbsa_type") == "metro")
        & pl.col("area_code").is_in(list(usable))
    )
    for row in sub.group_by(["state_fips", "area_code"]).agg(pl.len()).iter_rows(named=True):
        st = fips_to_state.get(str(row["state_fips"]))
        if st:
            out.setdefault(st, []).append(str(row["area_code"]))
    return {k: sorted(v) for k, v in out.items()}


#: State shares, and each state's tilt toward the low-rate 2020-21 cohorts.
#: The tilt is what generates cross-sectional exposure variation.
STATE_MIX: tuple[tuple[str, str, float, float], ...] = (
    # (state, fips, population share, low-coupon tilt in [-1, 1])
    ("CA", "06", 0.150, 0.75),
    ("TX", "48", 0.110, -0.35),
    ("FL", "12", 0.085, -0.50),
    ("NY", "36", 0.075, 0.45),
    ("PA", "42", 0.045, 0.20),
    ("IL", "17", 0.045, 0.55),
    ("OH", "39", 0.040, 0.15),
    ("GA", "13", 0.040, -0.30),
    ("NC", "37", 0.035, -0.25),
    ("MI", "26", 0.032, 0.30),
    ("NJ", "34", 0.030, 0.50),
    ("VA", "51", 0.028, 0.10),
    ("WA", "53", 0.028, 0.65),
    ("AZ", "04", 0.026, -0.55),
    ("MA", "25", 0.025, 0.60),
    ("TN", "47", 0.022, -0.40),
    ("IN", "18", 0.021, 0.05),
    ("MO", "29", 0.020, 0.00),
    ("MD", "24", 0.020, 0.35),
    ("WI", "55", 0.019, 0.25),
    ("CO", "08", 0.019, 0.40),
    ("MN", "27", 0.018, 0.30),
    ("SC", "45", 0.017, -0.45),
    ("NV", "32", 0.012, -0.60),
    ("UT", "49", 0.012, -0.20),
    ("OR", "41", 0.013, 0.45),
    ("ID", "16", 0.008, -0.65),
    ("MT", "30", 0.005, -0.30),
    ("ME", "23", 0.005, 0.00),
    ("WV", "54", 0.005, 0.10),
)

SELLERS = ("BIG BANK CORP", "REGIONAL LENDER LLC", "NONBANK ORIGINATOR INC", "Other sellers")
SERVICERS = ("BIG BANK SERVICING", "MIDSIZE SERVICER LP", "Other servicers")


@dataclass(frozen=True, slots=True)
class FixtureSummary:
    cohorts: list[str]
    n_loans: int
    n_performance_rows: int
    performance_period: str
    files: list[str]
    seed: int
    dgp_parameters: dict[str, float]


def _cohort_to_date(cohort: str) -> date:
    year = int(cohort[:4])
    q = int(cohort[-1])
    return date(year, (q - 1) * 3 + 1, 1)


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def _add_months(d: date, k: int) -> date:
    total = (d.year * 12 + d.month - 1) + k
    return date(total // 12, total % 12 + 1, 1)


def _yyyymm(d: date) -> str:
    return f"{d.year:04d}{d.month:02d}"


def _parse_month(s: str) -> date:
    """Parse a ``YYYY-MM`` config value into the first of that month."""
    year, month = s.split("-")
    return date(int(year), int(month), 1)


def _market_rate_path(monthly_rates: pl.DataFrame, first: date, last: date) -> dict[date, float]:
    """Real PMMS monthly path, used both to set coupons and to drive the hazard."""
    sub = monthly_rates.filter((pl.col("period") >= first) & (pl.col("period") <= last)).drop_nulls(
        "market_rate"
    )
    return dict(zip(sub["period"].to_list(), sub["market_rate"].to_list(), strict=True))


def generate(
    cfg: Config,
    monthly_rates: pl.DataFrame,
    out_dir: Path | None = None,
) -> FixtureSummary:
    """Write schema-exact synthetic origination and performance files.

    ``monthly_rates`` is the output of :func:`lockin.rates.monthly_market_rate` --
    the **real** PMMS path. Using it means the synthetic coupon distribution and
    the synthetic prepayment behaviour are driven by the actual history of U.S.
    mortgage rates, which makes the engineering test realistic without making the
    output empirical.
    """
    rng = np.random.default_rng(cfg.mortgage.synthetic_seed)
    out = out_dir or cfg.path("fixtures", "freddie")
    out.mkdir(parents=True, exist_ok=True)

    perf_start = _parse_month(cfg.mortgage.performance_start)
    perf_end = _parse_month(cfg.mortgage.performance_end)
    rate_path = _market_rate_path(monthly_rates, date(1999, 1, 1), perf_end)
    if not rate_path:
        raise ValueError("no PMMS monthly rates available for the fixture window")

    states = np.array([s[0] for s in STATE_MIX])
    state_share = np.array([s[2] for s in STATE_MIX], dtype=float)
    state_share = state_share / state_share.sum()
    state_tilt = {s[0]: s[3] for s in STATE_MIX}

    # MSA codes are drawn from the REAL OMB crosswalk so that the MSA-level code path
    # (crosswalk join, stability filter, metdiv discrimination) is genuinely exercised.
    # The codes are real; which synthetic loan gets which code is arbitrary, so no
    # MSA-level number from fixtures means anything about that metro. When the crosswalk
    # is not cached the field stays null and the MSA path is simply untested, which the
    # manifest records rather than silently substituting made-up codes.
    msa_by_state = _fixture_msa_codes(cfg)

    all_files: list[str] = []
    total_loans = 0
    total_perf = 0

    for cohort in cfg.mortgage.cohorts:
        n = cfg.mortgage.synthetic_loans_per_cohort
        orig_date = _cohort_to_date(cohort)
        first_pay = _add_months(orig_date, 2)
        cohort_market = _nearest_rate(rate_path, orig_date)

        # State assignment: tilt toward states whose "low-coupon tilt" matches how
        # low this cohort's market rate is relative to the sample mean.
        mean_rate = float(np.mean(list(rate_path.values())))
        lowness = (mean_rate - cohort_market) / max(mean_rate, 1e-9)
        tilt_vec = np.array([1.0 + 1.2 * lowness * state_tilt[s] for s in states])
        probs = state_share * np.clip(tilt_vec, 0.05, None)
        probs = probs / probs.sum()
        st = rng.choice(states, size=n, p=probs)

        # Draw an MSA within the assigned state. Larger metros are more likely, which
        # is approximated by weighting toward the lower (older, larger-metro) codes
        # only weakly -- the shape does not matter for a code-path test, but a single
        # metro per state would leave the multi-MSA-per-state join untested.
        msa = np.array([""] * n, dtype=object)
        if msa_by_state:
            for s in np.unique(st):
                pool = msa_by_state.get(str(s), [])
                if not pool:
                    continue
                idx = np.flatnonzero(st == s)
                picks = rng.choice(pool, size=idx.size)
                in_metro = rng.random(idx.size) >= NON_METRO_SHARE
                msa[idx[in_metro]] = picks[in_metro]

        # Note rates: centred on the cohort's real market rate, with dispersion.
        note = (
            cohort_market
            + rng.normal(0.0, 0.30, n)
            + rng.choice([-0.25, 0.0, 0.25, 0.5], size=n, p=[0.15, 0.5, 0.25, 0.10])
        )
        note = np.round(np.clip(note, 0.5, 12.0) * 8.0) / 8.0  # eighths, as in practice

        term = np.where(rng.random(n) < 0.85, 360, 180).astype(int)
        upb = (rng.lognormal(mean=12.2, sigma=0.45, size=n) // 1000 * 1000).clip(25_000, 900_000)
        ltv = np.clip(rng.normal(76, 13, n), 20, 97).round().astype(int)
        cltv = np.clip(ltv + rng.integers(0, 6, n), ltv, 105).astype(int)
        fico = np.clip(rng.normal(745, 45, n), 300, 850).round().astype(int)
        dti = np.clip(rng.normal(35, 9, n), 5, 65).round().astype(int)
        purpose = rng.choice(["P", "C", "N"], size=n, p=[0.45, 0.20, 0.35])
        occ = rng.choice(["P", "I", "S"], size=n, p=[0.90, 0.07, 0.03])
        ptype = rng.choice(["SF", "CO", "PU", "MH"], size=n, p=[0.76, 0.10, 0.13, 0.01])
        fthb = np.where(purpose == "P", rng.choice(["Y", "N"], size=n, p=[0.33, 0.67]), "9")
        units = rng.choice([1, 2, 3, 4], size=n, p=[0.96, 0.028, 0.008, 0.004])
        mi = np.where(ltv > 80, rng.integers(12, 35, n), 0)
        channel = rng.choice(["R", "B", "C", "T"], size=n, p=[0.45, 0.12, 0.38, 0.05])
        seller = rng.choice(SELLERS, size=n, p=[0.35, 0.25, 0.25, 0.15])
        servicer = rng.choice(SERVICERS, size=n, p=[0.45, 0.30, 0.25])
        zip3 = rng.integers(100, 999, n) * 100
        nborr = rng.choice([1, 2], size=n, p=[0.42, 0.58])
        # Deliberate sentinel injection so the NA-normalisation path is exercised.
        fico_out = np.where(rng.random(n) < 0.004, 9999, fico)
        dti_out = np.where(rng.random(n) < 0.010, 999, dti)
        ltv_out = np.where(rng.random(n) < 0.002, 999, ltv)

        seq = np.array([f"F{cohort[2:4]}{cohort[4:6]}{i:07d}" for i in range(1, n + 1)])
        maturity = _add_months(first_pay, int(term.max()))

        orig_rows = []
        for i in range(n):
            mat_i = _add_months(first_pay, int(term[i]) - 1)
            orig_rows.append(
                [
                    str(fico_out[i]),
                    _yyyymm(first_pay),
                    str(fthb[i]),
                    _yyyymm(mat_i),
                    str(msa[i]),  # real stable metro CBSA code, or "" for non-metro
                    str(int(mi[i])) if mi[i] > 0 else "0",
                    str(int(units[i])),
                    str(occ[i]),
                    str(int(cltv[i])),
                    str(int(dti_out[i])),
                    str(int(upb[i])),
                    str(int(ltv_out[i])),
                    f"{note[i]:.3f}",
                    str(channel[i]),
                    "N",
                    "FRM",
                    str(st[i]),
                    str(ptype[i]),
                    f"{int(zip3[i]):05d}",
                    str(seq[i]),
                    str(purpose[i]),
                    str(int(term[i])),
                    f"{int(nborr[i]):02d}",
                    str(seller[i]),
                    str(servicer[i]),
                    "",
                    "",
                    "9",
                    "",
                    "2",
                    "N",
                    "",
                ]
            )

        orig_path = out / f"historical_data_{cohort}.txt"
        orig_path.write_text("\n".join("|".join(r) for r in orig_rows) + "\n")
        all_files.append(orig_path.name)
        total_loans += n

        # --- performance simulation -------------------------------------------
        perf_lines: list[str] = []
        # Left truncation: acquisition lag of 0-4 months for a random subset.
        acq_lag = rng.choice([0, 1, 2, 3, 4], size=n, p=[0.55, 0.22, 0.12, 0.07, 0.04])

        first_obs = [max(_add_months(first_pay, int(acq_lag[i])), perf_start) for i in range(n)]
        alive = np.ones(n, dtype=bool)

        for period_idx in range(_months_between(perf_start, perf_end) + 1):
            period = _add_months(perf_start, period_idx)
            mkt = _nearest_rate(rate_path, period)
            for i in np.flatnonzero(alive):
                if period < first_obs[i]:
                    continue
                age = _months_between(first_pay, period) + 1
                if age < 1:
                    continue
                rem_term = int(term[i]) - age
                if rem_term <= 0:
                    alive[i] = False
                    continue
                bal = float(
                    remaining_balance(float(upb[i]), float(note[i]), float(term[i]), float(age))
                )
                # Point-in-time refinance incentive drives the prepayment hazard.
                incentive = float(note[i]) - mkt
                age_term = PREPAY_AGE_RAMP * min(age, PREPAY_AGE_PLATEAU)
                logit_pp = (
                    PREPAY_BASE_LOGIT
                    + PREPAY_RATE_GAP_COEF * incentive
                    + age_term
                    - 0.004 * (float(fico[i]) - 745.0) / 10.0
                )
                p_pp = 1.0 / (1.0 + np.exp(-logit_pp))
                cur_ltv_proxy = float(ltv[i]) * bal / max(float(upb[i]), 1.0)
                logit_ce = CREDIT_BASE_LOGIT + CREDIT_LTV_COEF * (cur_ltv_proxy - 75.0)
                p_ce = 1.0 / (1.0 + np.exp(-logit_ce))

                u = rng.random()
                zb, zb_date, mod_flag = "", "", ""
                dq = "0"
                if u < p_pp:
                    zb, zb_date = "01", _yyyymm(period)
                    alive[i] = False
                elif u < p_pp + p_ce:
                    zb = str(rng.choice(["02", "03", "09"], p=[0.35, 0.30, 0.35]))
                    zb_date = _yyyymm(period)
                    dq = "3"
                    alive[i] = False
                elif u < p_pp + p_ce + ADMIN_REMOVAL_MONTHLY_PROB:
                    zb = str(rng.choice(["15", "16", "96"], p=[0.55, 0.35, 0.10]))
                    zb_date = _yyyymm(period)
                    alive[i] = False
                elif rng.random() < MODIFICATION_MONTHLY_PROB:
                    mod_flag = "Y"
                    dq = "1"

                cur_upb = 0.0 if zb else bal
                perf_lines.append(
                    "|".join(
                        [
                            str(seq[i]),
                            _yyyymm(period),
                            f"{cur_upb:.2f}",
                            dq,
                            str(age),
                            str(rem_term),
                            "",
                            mod_flag,
                            zb,
                            zb_date,
                            f"{note[i]:.3f}",
                            "0",
                            _yyyymm(_add_months(period, -1)),
                            "",
                            "",
                            "",
                            "",
                            "",
                            "",
                            "",
                            "",
                            "",
                            "",
                            "",
                            "",
                            str(round(cur_ltv_proxy)),
                            f"{bal:.2f}" if zb else "",
                            "",
                            "",
                            "",
                            "",
                            f"{cur_upb:.2f}",
                        ]
                    )
                )

        perf_path = out / f"historical_data_time_{cohort}.txt"
        perf_path.write_text("\n".join(perf_lines) + "\n")
        all_files.append(perf_path.name)
        total_perf += len(perf_lines)
        _ = maturity

    summary = FixtureSummary(
        cohorts=list(cfg.mortgage.cohorts),
        n_loans=total_loans,
        n_performance_rows=total_perf,
        performance_period=f"{cfg.mortgage.performance_start}..{cfg.mortgage.performance_end}",
        files=all_files,
        seed=cfg.mortgage.synthetic_seed,
        dgp_parameters={
            "PREPAY_RATE_GAP_COEF": PREPAY_RATE_GAP_COEF,
            "PREPAY_BASE_LOGIT": PREPAY_BASE_LOGIT,
            "PREPAY_AGE_RAMP": PREPAY_AGE_RAMP,
            "CREDIT_BASE_LOGIT": CREDIT_BASE_LOGIT,
            "CREDIT_LTV_COEF": CREDIT_LTV_COEF,
            "ADMIN_REMOVAL_MONTHLY_PROB": ADMIN_REMOVAL_MONTHLY_PROB,
            "MODIFICATION_MONTHLY_PROB": MODIFICATION_MONTHLY_PROB,
        },
    )

    write_manifest(
        out,
        name="freddie_llds_synthetic_fixtures",
        source="SYNTHETIC -- generated by lockin.fixtures.generate",
        source_url="n/a (synthetic)",
        license_terms="Synthetic; freely redistributable.",
        redistribution_status="synthetic -- freely redistributable",
        schema_version=SCHEMA_VERSION,
        row_count=total_loans + total_perf,
        geographic_level="state",
        coverage_period=summary.performance_period,
        known_limitations=[
            "SYNTHETIC. Not data about the United States. Any estimate computed "
            "from these files recovers the parameters of the simple behavioural "
            "model in lockin/fixtures.py, not a fact about American homeowners.",
            "Schema-exact to the official Freddie Mac layout so that parsers, "
            "joins, event rules, and estimators are genuinely exercised.",
            "Note-rate levels and the monthly hazard are driven by the REAL PMMS "
            "path, so the coupon distribution across cohorts is realistically "
            "shaped -- this makes the engineering test realistic, not empirical.",
            "MSA codes are REAL, composition-stable metropolitan CBSA codes taken from "
            "the OMB crosswalk, so the MSA code path is exercised. Which synthetic loan "
            "receives which code is ARBITRARY -- no MSA-level number computed from these "
            "fixtures says anything about that metro. When the crosswalk is not cached "
            "the field is null and the MSA path is untested.",
            "No ARM loans; no HARP/Relief Refinance loans; no super-conforming loans.",
        ],
        data_class="SYNTHETIC",
        extra={
            "seed": cfg.mortgage.synthetic_seed,
            "dgp_parameters": summary.dgp_parameters,
            "cohorts": summary.cohorts,
            "n_loans": total_loans,
            "n_performance_rows": total_perf,
            "origination_columns": list(ORIGINATION_COLUMNS),
            "performance_columns": list(PERFORMANCE_COLUMNS),
        },
    )
    return summary


def _nearest_rate(rate_path: dict[date, float], when: date) -> float:
    """Rate at ``when``, else the latest earlier rate, else the earliest available."""
    if when in rate_path:
        return rate_path[when]
    earlier = [d for d in rate_path if d <= when]
    if earlier:
        return rate_path[max(earlier)]
    return rate_path[min(rate_path)]
