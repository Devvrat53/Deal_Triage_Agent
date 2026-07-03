"""Step 4 — triage a deal against the mandate.

Two parts:
  1. The model reads the deal facts + the mandate sections we retrieved, and fills in
     GateFindings — its judgment on the four SEMANTIC gates (sector / geography / stage /
     quality).
  2. map_decision() takes those findings, plus the numeric facts (size), the reader's
     figures-consistent flag, and the pipeline duplicate signal, and applies the mandate's
     §4 decision order in plain Python to pick the final Decision + reason code.

We keep the decision rules in code (not the model) so the reason codes are always
consistent and we can unit-test them.

    facts = await extract_deal_facts(path)
    result = await adjudicate(facts, retriever, duplicate_of=dup_id)
"""

from agent_framework import Agent
from . import config, prompts
from .models import Adjudication, DealFacts, Decision, GateFindings, GateResult, ReasonCode

# Mandate §3.4 revenue band (in millions).
SIZE_FLOOR_MUSD = 10.0
SIZE_CEILING_MUSD = 100.0
# Below this read confidence we don't trust the document at all and send it to review
# without calling the model. (Our teasers/decks read 0.9+, so this rarely fires.)
LOW_CONFIDENCE = 0.30


def grounding_queries(facts):
    """The questions we ask the mandate index for one deal."""
    sector = facts.sector or "the company's sector"
    hq = facts.hq_location or "the company's headquarters"
    return [
        f"Is '{sector}' an in-mandate sector or a hard-excluded sector?",
        f"Is a company headquartered in {hq} within the mandate geography?",
        "Stage requirement: post-revenue growth versus pre-revenue or seed stage",
        "Business quality thresholds: recurring revenue, year-over-year growth, net revenue retention",
        "Deal size band; handling incomplete, duplicate, or internally inconsistent submissions",
    ]


def passages_to_text(passages):
    """Format retrieved mandate sections into one block for the prompt."""
    return "\n\n".join(f"[{p.citation}]\n{p.content}" for p in passages)


def map_decision(findings, facts, duplicate_of=None):
    """Turn the model's GateFindings (+ facts, +duplicate signal) into a final Adjudication.

    We go through the gates in the mandate's §4 order; the FIRST one that decides wins.
    Hard-exclude gates (sector/geography/stage) run BEFORE the completeness, size,
    consistency and duplicate checks — so e.g. an excluded-sector company is a clean Pass
    even if it's also a duplicate or missing financials.
    """
    steps = []

    def check(name, passed, note):
        steps.append(GateResult(name=name, passed=passed, note=note))

    def done(decision, code, rationale):
        return Adjudication(
            decision=decision,
            reason_code=code,
            rationale=rationale,
            gates=steps,
            citations=findings.citations,
            duplicate_of=duplicate_of,
            confidence=findings.confidence,
            facts=facts,
        )

    # Gate 1 — Sector (§3.1). A hard-excluded sector ends the review.
    if findings.sector_hard_excluded is True:
        check("Gate 1 — Sector", False, findings.sector_note)
        return done(Decision.PASS, ReasonCode.X_SECTOR,
                    f"Hard-excluded sector — decline. {findings.sector_note}")
    if findings.sector_hard_excluded is None:
        check("Gate 1 — Sector", None, findings.sector_note)
        return done(Decision.NEEDS_REVIEW, ReasonCode.R_INCOMPLETE,
                    f"Could not determine the company's sector from the document. {findings.sector_note}")
    check("Gate 1 — Sector", True, f"{findings.mapped_sector or 'in-mandate'} — {findings.sector_note}")

    # Gate 2 — Geography (§3.2).
    if findings.geography_in_mandate is False:
        check("Gate 2 — Geography", False, findings.geography_note)
        return done(Decision.PASS, ReasonCode.X_GEO,
                    f"Headquartered outside the US/Canada — decline. {findings.geography_note}")
    if findings.geography_in_mandate is None:
        check("Gate 2 — Geography", None, findings.geography_note)
        return done(Decision.NEEDS_REVIEW, ReasonCode.R_INCOMPLETE,
                    f"Headquarters location isn't stated. {findings.geography_note}")
    check("Gate 2 — Geography", True, findings.geography_note)

    # Gate 3 — Stage (§3.3). Pre-revenue/seed is too early.
    if findings.stage_post_revenue is False:
        check("Gate 3 — Stage", False, findings.stage_note)
        return done(Decision.PASS, ReasonCode.X_STAGE,
                    f"Pre-revenue / seed stage — too early for growth equity. {findings.stage_note}")
    # None on stage doesn't block: the presence of real revenue below speaks for itself.
    check("Gate 3 — Stage", findings.stage_post_revenue, findings.stage_note)

    # Pre-check — enough to screen against the size / quality bar? (§5)
    if facts.revenue is None or facts.yoy_growth_pct is None:
        missing = []
        if facts.revenue is None:
            missing.append("revenue/ARR")
        if facts.yoy_growth_pct is None:
            missing.append("growth rate")
        note = "Missing " + " and ".join(missing)
        check("Completeness", False, note)
        return done(Decision.NEEDS_REVIEW, ReasonCode.R_INCOMPLETE,
                    f"Key financials missing ({note}) — can't screen against the size/quality bar. "
                    "Request full financials from the broker.")
    check("Completeness", True, "Revenue and growth are stated")

    # Gate 4 — Size band (§3.4). Below floor = clean Pass; above band = strategy-fit review.
    if facts.revenue < SIZE_FLOOR_MUSD:
        check("Gate 4 — Size", False, f"Revenue ${facts.revenue}M below the ${SIZE_FLOOR_MUSD:.0f}M floor")
        return done(Decision.PASS, ReasonCode.X_SIZE_LOW,
                    f"Below the ${SIZE_FLOOR_MUSD:.0f}M revenue floor (${facts.revenue}M) — pre-scale for this strategy.")
    if facts.revenue > SIZE_CEILING_MUSD:
        check("Gate 4 — Size", None, f"Revenue ${facts.revenue}M above the ${SIZE_CEILING_MUSD:.0f}M band")
        return done(Decision.NEEDS_REVIEW, ReasonCode.R_SIZE_HIGH,
                    f"Above the core growth-equity band (${facts.revenue}M > ${SIZE_CEILING_MUSD:.0f}M) — "
                    "a strategy-fit call for the Investments team, not an automatic decline.")
    check("Gate 4 — Size", True, f"Revenue ${facts.revenue}M is within the ${SIZE_FLOOR_MUSD:.0f}-{SIZE_CEILING_MUSD:.0f}M band")

    # Integrity — do the document's own figures agree? (§7) (flag comes from the reader)
    if facts.figures_consistent is False:
        detail = "; ".join(facts.observations) if facts.observations else ""
        check("Integrity — figures", False, "Document contradicts itself on a key metric")
        return done(Decision.NEEDS_REVIEW, ReasonCode.R_INCONSISTENT,
                    f"The document's own figures disagree — verify with the broker before screening. {detail}")
    check("Integrity — figures", True, "Figures are internally consistent")

    # Pipeline — already seen this company? (§6) (signal comes from Cosmos)
    if duplicate_of:
        check("Pipeline — duplicate", False, f"Same company already in the pipeline ({duplicate_of})")
        return done(Decision.NEEDS_REVIEW, ReasonCode.R_DUP,
                    f"Probable duplicate of an existing pipeline entry ({duplicate_of}). "
                    "Confirm it's the same company and reconcile which source's figures to trust.")
    check("Pipeline — duplicate", True, "No existing pipeline entry for this company")

    # Gate 5 — Business quality (§3.5), judgment incl. trend.
    if findings.quality_meets_bar is False:
        check("Gate 5 — Quality", False, findings.quality_note)
        return done(Decision.NEEDS_REVIEW, ReasonCode.R_QUALITY,
                    f"Clears the mandate on paper but the quality metrics are soft or declining — human review. "
                    f"{findings.quality_note}")
    if findings.quality_meets_bar is None:
        check("Gate 5 — Quality", None, findings.quality_note)
        return done(Decision.NEEDS_REVIEW, ReasonCode.R_QUALITY,
                    f"Quality can't be fully assessed from what's disclosed — human review. {findings.quality_note}")
    check("Gate 5 — Quality", True, findings.quality_note)

    # Clears every gate.
    return done(Decision.PURSUE, ReasonCode.FIT_OK,
                "Clears every mandate gate — advance to the Investments team for full evaluation and diligence.")


def build_adjudicate_agent():
    """The agent that fills in GateFindings. Build once and reuse."""
    return Agent(client=config.build_chat_client(), instructions=prompts.ADJUDICATION_SYSTEM)


async def adjudicate(facts, retriever, agent=None, duplicate_of=None):
    """Retrieve the relevant mandate sections, ask the model, then map to a decision."""
    agent = agent or build_adjudicate_agent()

    # If we couldn't read the document at all, send it to review without the model.
    if facts.extraction_confidence < LOW_CONFIDENCE:
        return Adjudication(
            decision=Decision.NEEDS_REVIEW,
            reason_code=ReasonCode.R_INCOMPLETE,
            rationale=f"Could not read the document reliably (confidence {facts.extraction_confidence}). Human review.",
            gates=[GateResult(name="Readability", passed=False, note="document unreadable")],
            citations=[],
            duplicate_of=duplicate_of,
            confidence=facts.extraction_confidence,
            facts=facts,
        )

    passages = await retriever.grounding_for(grounding_queries(facts))
    prompt = prompts.ADJUDICATION_USER.format(
        facts=facts.model_dump_json(indent=2),
        passages=passages_to_text(passages),
    )
    response = await agent.run(prompt, options={"response_format": GateFindings})
    return map_decision(response.value, facts, duplicate_of)
