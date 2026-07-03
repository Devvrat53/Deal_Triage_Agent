"""Step 5 — the human-in-the-loop workflow.

This wraps "extract -> dedup-check -> adjudicate" in a workflow that PAUSES for a human
reviewer before the deal is finished, using the Agent Framework's request_info mechanism:

    intake:   read deal -> (Cosmos) check pipeline for duplicates -> adjudicate
              -> ctx.request_info(...)                              # PAUSE here
    (the Investments team member reviews and picks an action)
    finalize: (Cosmos) record the human's decision -> ctx.yield_output(DealRecord)  # RESUME

Both tools meet here: Azure AI Search grounds the recommendation (inside adjudicate), and
Azure Cosmos DB is read before the decision (dedup) and written after it (system of record).
The write happens on the HUMAN's action, never the AI's.

How to drive it (the same workflow object is reused across the pause):
    wf = build_workflow()
    async for ev in wf.run(DealInput(...), stream=True):
        if ev.type == "request_info":
            request_id, review = ev.request_id, ev.data       # show review to the human
    async for ev in wf.run(responses={request_id: AnalystAction(...)}, stream=True):
        if ev.type == "output":
            record = ev.data                                  # DealRecord (persisted)

Not adding `from __future__ import annotations` to this file — it breaks the
Agent Framework's check on the WorkflowContext type hints below.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Never

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler, response_handler
from .adjudicator import adjudicate, build_adjudicate_agent
from .extraction import build_extract_agent, extract_deal_facts
from .models import (Adjudication, AnalystAction, AnalystActionType, DealFacts, DealFlags,
                     DealRecord, Decision, FinalOutcome)
from .pipeline import DealPipeline, normalize_company
from .retrieval import Retriever


@dataclass
class DealInput:
    """What starts the workflow: one deal document to triage."""
    deal_path: str


@dataclass
class DealReview:
    """What the reviewer sees when the workflow pauses."""
    deal_path: str
    facts: DealFacts
    recommendation: Adjudication


# Which analyst action means "I agree with the agent" for each recommendation.
AGREEING_ACTION = {
    Decision.PURSUE: AnalystActionType.PURSUE,
    Decision.PASS: AnalystActionType.PASS,
    Decision.NEEDS_REVIEW: AnalystActionType.HOLD,
}
# The final decision a given analyst action records.
ACTION_DECISION = {
    AnalystActionType.PURSUE: Decision.PURSUE,
    AnalystActionType.PASS: Decision.PASS,
    AnalystActionType.HOLD: Decision.NEEDS_REVIEW,
}


class DealReviewExecutor(Executor):
    """One step: read the deal, dedup-check it, adjudicate, pause for the human, then record."""

    def __init__(self, retriever, pipeline, id="deal_review"):
        super().__init__(id=id)
        self.retriever = retriever
        self.pipeline = pipeline
        self.extract_agent = build_extract_agent()
        self.adjudicate_agent = build_adjudicate_agent()

    @handler
    async def intake(self, deal: DealInput, ctx: WorkflowContext[Never, DealRecord]) -> None:
        facts = await extract_deal_facts(deal.deal_path, agent=self.extract_agent)
        deal_id = Path(deal.deal_path).stem
        company_key = normalize_company(facts.company_name)

        # TOOL 2 (read): has this company already come through the pipeline? (mandate §6)
        # Cosmos SDK is synchronous, so run it off the event loop.
        duplicate_of = None
        if company_key:
            dups = await asyncio.to_thread(self.pipeline.find_duplicates, company_key, deal_id)
            if dups:
                duplicate_of = dups[0]["id"]

        recommendation = await adjudicate(
            facts, self.retriever, agent=self.adjudicate_agent, duplicate_of=duplicate_of,
        )
        review = DealReview(deal.deal_path, facts, recommendation)
        # PAUSE: emit the review and wait for the reviewer's AnalystAction.
        await ctx.request_info(request_data=review, response_type=AnalystAction)

    @response_handler
    async def finalize(self, request: DealReview, action: AnalystAction,
                       ctx: WorkflowContext[Never, DealRecord]) -> None:
        facts = request.facts
        rec = request.recommendation
        final_decision = ACTION_DECISION.get(action.action, rec.decision)
        deal_id = Path(request.deal_path).stem

        record = DealRecord(
            id=deal_id,
            company_key=normalize_company(facts.company_name) or deal_id,
            company_name=facts.company_name,
            source_file=Path(request.deal_path).name,
            broker=facts.broker,
            received_date=facts.received_date,
            facts=facts,
            recommendation=rec,
            flags=DealFlags(
                duplicate_of=rec.duplicate_of,
                inconsistent_figures=(facts.figures_consistent is False),
            ),
            final=FinalOutcome(
                decision=final_decision,
                agreed_with_ai=(rec.decision == final_decision),
                decided_by="analyst",
                note=action.note,
                decided_at=datetime.now(timezone.utc).isoformat(),
            ),
            status="decided",
        )
        # TOOL 2 (write): persist the HUMAN's decision to the pipeline system of record.
        await asyncio.to_thread(self.pipeline.record_decision, record.model_dump(mode="json"))
        await ctx.yield_output(record)


def build_workflow(retriever=None, pipeline=None):
    """Create the workflow. Pass shared clients so we don't rebuild them each deal."""
    retriever = retriever or Retriever()
    pipeline = pipeline or DealPipeline()
    return WorkflowBuilder(start_executor=DealReviewExecutor(retriever, pipeline)).build()


# Command-line demo: `python -m src.workflow <deal>` — shows the pause/resume.
async def _demo(deal_path):
    wf = build_workflow()

    request_id, review = None, None
    async for ev in wf.run(DealInput(deal_path=deal_path), stream=True):
        if ev.type == "request_info":
            request_id, review = ev.request_id, ev.data

    rec = review.recommendation
    f = review.facts
    print(f"\n=== Deal review: {review.deal_path} ===")
    print(f"Extracted: {f.company_name} | {f.sector} | {f.hq_location} | "
          f"{f.revenue}M {f.revenue_type or ''} | growth {f.yoy_growth_pct}")
    print(f"\nAGENT RECOMMENDS: {rec.decision.value}  ({rec.reason_code.value})  confidence={rec.confidence:.2f}")
    print(f"Rationale: {rec.rationale}")
    for g in rec.gates:
        print(f"   [{str(g.passed):5s}] {g.name}: {g.note}")
    if rec.duplicate_of:
        print(f"   ! duplicate of pipeline entry: {rec.duplicate_of}")

    choice = input("\nAction — [p]ursue / [x] pass / [h]old for review: ").strip().lower()
    actions = {"p": AnalystActionType.PURSUE, "x": AnalystActionType.PASS, "h": AnalystActionType.HOLD}
    action = actions.get(choice, AGREEING_ACTION[rec.decision])
    note = input("Note (optional): ").strip()

    async for ev in wf.run(responses={request_id: AnalystAction(action=action, note=note)}, stream=True):
        if ev.type == "output":
            record = ev.data
            print(f"\n=== RECORDED to pipeline === decision: {record.final.decision.value} "
                  f"(agreed: {record.final.agreed_with_ai})")
            if record.final.note:
                print(f"Note: {record.final.note}")


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "data/deals/01_meridian_ops_teaser.pdf"
    asyncio.run(_demo(path))
