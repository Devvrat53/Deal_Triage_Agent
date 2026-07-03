"""Offline regression tests for the deterministic decision mapping (mandate §4).

These never call the model — they pin the policy that turns GateFindings (+ facts,
+ duplicate signal) into a Decision + ReasonCode. Run:
    python -m tests.test_adjudicator_mapping
"""

from src.adjudicator import map_decision
from src.models import DealFacts, Decision, GateFindings, ReasonCode


def F(**kw) -> DealFacts:
    """A clean, in-band deal by default; override fields per case."""
    base = dict(
        company_name="Acme", sector="B2B SaaS", hq_location="Austin, TX, USA",
        revenue=30.0, revenue_type="ARR", yoy_growth_pct=40.0,
        recurring_pct=90.0, nrr_pct=110.0, figures_consistent=True,
        extraction_confidence=0.95,
    )
    base.update(kw)
    return DealFacts(**base)


def G(**kw) -> GateFindings:
    """All four semantic gates passing by default; override per case."""
    base = dict(
        mapped_sector="B2B SaaS", sector_hard_excluded=False, sector_note="ok",
        geography_in_mandate=True, geography_note="ok",
        stage_post_revenue=True, stage_note="ok",
        quality_meets_bar=True, quality_note="ok", confidence=0.9,
    )
    base.update(kw)
    return GateFindings(**base)


# label, findings, facts, duplicate_of, expected decision, expected reason code
CASES = [
    # --- the 11 locked sample deals ---
    ("#1  clean SaaS",        G(), F(),                                              None,          Decision.PURSUE,       ReasonCode.FIT_OK),
    ("#2  fintech profitable",G(), F(sector="fintech infra", hq_location="Toronto, ON", nrr_pct=112, yoy_growth_pct=27, recurring_pct=96), None, Decision.PURSUE, ReasonCode.FIT_OK),
    ("#3  crypto sector",     G(sector_hard_excluded=True),                          F(),           None,          Decision.PASS,         ReasonCode.X_SECTOR),
    ("#4  Berlin geography",  G(geography_in_mandate=False),                         F(hq_location="Berlin, Germany"), None, Decision.PASS,   ReasonCode.X_GEO),
    ("#5  pre-revenue stage", G(stage_post_revenue=False),                           F(revenue=None, yoy_growth_pct=None), None, Decision.PASS, ReasonCode.X_STAGE),
    ("#6  below size floor",  G(), F(revenue=3.1, yoy_growth_pct=58),                 None,          Decision.PASS,         ReasonCode.X_SIZE_LOW),
    ("#7  above size band",   G(), F(revenue=248.0, yoy_growth_pct=9),               None,          Decision.NEEDS_REVIEW, ReasonCode.R_SIZE_HIGH),
    ("#8  missing financials",G(), F(revenue=None, yoy_growth_pct=None),             None,          Decision.NEEDS_REVIEW, ReasonCode.R_INCOMPLETE),
    ("#9  weak/declining",    G(quality_meets_bar=False),                            F(yoy_growth_pct=12, nrr_pct=94), None, Decision.NEEDS_REVIEW, ReasonCode.R_QUALITY),
    ("#10 duplicate",         G(), F(),                                              "01_meridian", Decision.NEEDS_REVIEW, ReasonCode.R_DUP),
    ("#11 inconsistent nums", G(), F(figures_consistent=False),                      None,          Decision.NEEDS_REVIEW, ReasonCode.R_INCONSISTENT),

    # --- ordering: hard-excludes short-circuit before integrity/pipeline/quality ---
    ("excluded beats dup",    G(sector_hard_excluded=True),                          F(figures_consistent=False), "x", Decision.PASS, ReasonCode.X_SECTOR),
    ("size-low beats integ",  G(), F(revenue=3.0, yoy_growth_pct=50, figures_consistent=False), "x", Decision.PASS, ReasonCode.X_SIZE_LOW),
    ("integ beats dup",       G(), F(figures_consistent=False),                      "x",           Decision.NEEDS_REVIEW, ReasonCode.R_INCONSISTENT),
    ("dup beats quality",     G(quality_meets_bar=False),                            F(),           "x",           Decision.NEEDS_REVIEW, ReasonCode.R_DUP),

    # --- tri-state None handling ---
    ("sector unclear",        G(sector_hard_excluded=None),                          F(),           None,          Decision.NEEDS_REVIEW, ReasonCode.R_INCOMPLETE),
    ("geography unclear",     G(geography_in_mandate=None),                          F(),           None,          Decision.NEEDS_REVIEW, ReasonCode.R_INCOMPLETE),
    ("stage unclear -> ok",   G(stage_post_revenue=None),                            F(),           None,          Decision.PURSUE,       ReasonCode.FIT_OK),
    ("quality unclear",       G(quality_meets_bar=None),                             F(),           None,          Decision.NEEDS_REVIEW, ReasonCode.R_QUALITY),
]


def run() -> int:
    failures = 0
    for label, findings, facts, dup, exp_dec, exp_code in CASES:
        adj = map_decision(findings, facts, duplicate_of=dup)
        ok = adj.decision == exp_dec and adj.reason_code == exp_code
        print(f"{'PASS' if ok else 'FAIL'}  {label:24s} -> {adj.decision.value}/{adj.reason_code.value}"
              + ("" if ok else f"  (expected {exp_dec.value}/{exp_code.value})"))
        failures += not ok
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    return failures


if __name__ == "__main__":
    raise SystemExit(1 if run() else 0)
