"""Step 6 — Streamlit UI: the human-in-the-loop deal-triage queue.

An Investments team member picks a deal from the queue; the agent reads the teaser/deck,
checks the pipeline for duplicates (Cosmos), triages it against the mandate (Azure AI
Search), and surfaces a recommended decision with gate-by-gate reasoning and citations.
The reviewer pursues, passes, or holds — the human always makes the final call, and only
then is the decision written to the pipeline system of record.

Under the hood this drives the Agent Framework HITL workflow (src/workflow.py): each deal
runs to a request_info pause; the reviewer's action resumes it to a persisted DealRecord.

Run:  streamlit run app.py
"""

import asyncio
import os
from pathlib import Path

import streamlit as st

# On Streamlit Community Cloud there is no .env file — the keys live in the app's
# Secrets store. Copy them into environment variables so config.py's os.getenv() finds
# them. Runs before the src imports below (config.py reads env vars at import time).
# Locally there are no Streamlit secrets, so this is a harmless no-op and .env is used.
try:
    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except Exception:
    pass

from src.extraction import IMAGE_TYPES
from src.models import AnalystAction, AnalystActionType, DealRecord, Decision
from src.pipeline import DealPipeline
from src.retrieval import Retriever
from src.workflow import DealInput, DealReview, build_workflow

DEALS_DIR = Path("data/deals")
DEAL_EXTS = {*IMAGE_TYPES, ".pdf", ".txt"}

DECISION_COLOR = {Decision.PURSUE: "🟢", Decision.PASS: "🔴", Decision.NEEDS_REVIEW: "🟡"}
ACTION_LABEL = {
    AnalystActionType.PURSUE: "Pursue",
    AnalystActionType.PASS: "Pass",
    AnalystActionType.HOLD: "Hold for review",
}

st.set_page_config(page_title="Northbridge — Deal Triage", layout="wide")


# --------------------------------------------------------------------------- #
# One persistent event loop across Streamlit reruns, so the async Azure clients
# (and a paused workflow) stay bound to a single loop.
# --------------------------------------------------------------------------- #
def run_async(coro):
    if "loop" not in st.session_state:
        st.session_state.loop = asyncio.new_event_loop()
    return st.session_state.loop.run_until_complete(coro)


def init_state() -> None:
    ss = st.session_state
    if "ready" in ss:
        return
    ss.retriever = Retriever()
    ss.pipeline = DealPipeline()                 # the second tool (Cosmos), shared
    ss.deals = sorted(p.name for p in DEALS_DIR.iterdir() if p.suffix.lower() in DEAL_EXTS)
    ss.reviews: dict[str, DealReview] = {}       # deal -> recommendation under review
    ss.pending: dict[str, tuple] = {}            # deal -> (workflow, request_id)
    ss.decisions: dict[str, DealRecord] = {}     # deal -> recorded outcome (this session)
    ss.selected = ss.deals[0]
    ss.ready = True


# --------------------------------------------------------------------------- #
# Workflow drivers
# --------------------------------------------------------------------------- #
async def _start(deal: str) -> tuple:
    """Run extract + dedup-check + adjudicate to the request_info pause."""
    wf = build_workflow(retriever=st.session_state.retriever, pipeline=st.session_state.pipeline)
    rid = review = None
    async for ev in wf.run(DealInput(deal_path=str(DEALS_DIR / deal)), stream=True):
        if ev.type == "request_info":
            rid, review = ev.request_id, ev.data
    return wf, rid, review


async def _resume(wf, rid: str, action: AnalystAction) -> DealRecord:
    record = None
    async for ev in wf.run(responses={rid: action}, stream=True):
        if ev.type == "output":
            record = ev.data
    return record


def ensure_reviewed(deal: str) -> None:
    """Lazily triage a deal the first time it is opened."""
    if deal in st.session_state.reviews:
        return
    with st.spinner(f"Reading, checking the pipeline, and triaging {deal}…"):
        wf, rid, review = run_async(_start(deal))
    st.session_state.reviews[deal] = review
    st.session_state.pending[deal] = (wf, rid)


def record(deal: str, action_type: AnalystActionType, note: str) -> None:
    """Resume the workflow with the human's action; finalize() writes it to Cosmos."""
    wf, rid = st.session_state.pending[deal]
    dealrecord = run_async(_resume(wf, rid, AnalystAction(action=action_type, note=note)))
    st.session_state.decisions[deal] = dealrecord
    del st.session_state.pending[deal]


# --------------------------------------------------------------------------- #
# Deal preview
# --------------------------------------------------------------------------- #
def render_deal(deal: str) -> None:
    path = DEALS_DIR / deal
    suffix = path.suffix.lower()
    if suffix in IMAGE_TYPES:
        st.image(str(path), width="stretch")
    elif suffix == ".pdf":
        import pymupdf

        with pymupdf.open(str(path)) as doc:
            for page in doc:                    # decks are multi-page — show them all
                st.image(page.get_pixmap(dpi=140).tobytes("png"), width="stretch")
    elif suffix == ".txt":
        st.code(path.read_text(encoding="utf-8", errors="replace"), language=None)


# --------------------------------------------------------------------------- #
# UI — sidebar queue
# --------------------------------------------------------------------------- #
def sidebar_queue() -> None:
    ss = st.session_state
    st.sidebar.title("📋 Deal queue")
    st.sidebar.caption("Northbridge Capital Partners · inbound deal triage")

    reviewed = len(ss.decisions)
    st.sidebar.progress(reviewed / len(ss.deals), text=f"{reviewed}/{len(ss.deals)} decided")

    st.sidebar.divider()
    for d in ss.deals:
        if d in ss.decisions:
            rec = ss.decisions[d]
            icon = "✅" if rec.final.agreed_with_ai else "✏️"
            label = f"{icon} {d}  ·  {rec.final.decision.value}"
        elif d in ss.reviews:
            rec = ss.reviews[d].recommendation
            label = f"{DECISION_COLOR[rec.decision]} {d}  ·  {rec.reason_code.value}"
        else:
            label = f"⚪ {d}"
        if st.sidebar.button(label, key=f"q_{d}", width="stretch"):
            ss.selected = d
            st.rerun()


# --------------------------------------------------------------------------- #
# UI — review tab (the detail panel)
# --------------------------------------------------------------------------- #
def detail_panel(deal: str) -> None:
    ss = st.session_state
    left, right = st.columns([5, 6], gap="large")

    with left:
        st.subheader("Deal document")
        render_deal(deal)

    with right:
        ensure_reviewed(deal)
        review: DealReview = ss.reviews[deal]
        facts = review.facts
        rec = review.recommendation

        st.subheader("Agent recommendation")
        c1, c2, c3 = st.columns(3)
        c1.metric("Decision", f"{DECISION_COLOR[rec.decision]} {rec.decision.value}")
        c2.metric("Reason", rec.reason_code.value)
        c3.metric("Confidence", f"{rec.confidence:.0%}")
        st.info(rec.rationale)
        if rec.duplicate_of:
            st.warning(f"⚠️ Probable duplicate of a deal already in the pipeline: **{rec.duplicate_of}**")

        with st.expander("Extracted deal facts", expanded=True):
            rev = f"{facts.revenue}M {facts.revenue_type or ''}".strip() if facts.revenue is not None else None
            st.write(
                {
                    "company": facts.company_name,
                    "sector": facts.sector,
                    "HQ": facts.hq_location,
                    "stage": facts.stage,
                    "revenue": rev,
                    "YoY growth %": facts.yoy_growth_pct,
                    "recurring %": facts.recurring_pct,
                    "NRR %": facts.nrr_pct,
                    "raise (ask) $M": facts.ask_amount,
                    "figures consistent": facts.figures_consistent,
                    "read confidence": facts.extraction_confidence,
                }
            )
            if facts.observations:
                st.caption("Observations: " + " · ".join(facts.observations))

        with st.expander("Gate-by-gate reasoning", expanded=True):
            for g in rec.gates:
                mark = {True: "✅", False: "❌", None: "⚠️"}.get(g.passed, "•")
                st.markdown(f"{mark} **{g.name}** — {g.note}")
        if rec.citations:
            with st.expander("Citations (mandate — source of truth)"):
                for c in rec.citations:
                    st.markdown(f"- {c}")

        st.divider()
        if deal in ss.decisions:
            _render_recorded(ss.decisions[deal])
        else:
            _render_actions(deal, rec)


def _render_actions(deal: str, rec) -> None:
    st.subheader("Your decision")
    st.caption("Confirm the recommendation or override it — you make the final advance-to-IC call.")
    note = st.text_input("Note / override reason / what to request from the broker", key=f"note_{deal}")

    b1, b2, b3 = st.columns(3)
    if b1.button("🟢 Pursue", key=f"pu_{deal}", width="stretch"):
        record(deal, AnalystActionType.PURSUE, note); st.rerun()
    if b2.button("🔴 Pass", key=f"pa_{deal}", width="stretch"):
        record(deal, AnalystActionType.PASS, note); st.rerun()
    if b3.button("🟡 Hold for review", key=f"ho_{deal}", width="stretch"):
        record(deal, AnalystActionType.HOLD, note); st.rerun()


def _render_recorded(rec: DealRecord) -> None:
    st.subheader("Recorded to pipeline")
    final = rec.final
    if final.agreed_with_ai:
        st.success(f"**{final.decision.value}** — confirmed the agent's recommendation. Written to Cosmos.")
    else:
        st.warning(f"**{final.decision.value}** — overrode the agent "
                   f"(recommended {rec.recommendation.decision.value}). Written to Cosmos.")
    if final.note:
        st.caption(f"Note: {final.note}")
    if st.button("Reopen", key=f"reopen_{rec.id}"):
        # A finished workflow can't be resumed again, so clear the cached review/outcome
        # and let it re-triage cleanly on next open.
        for store in (st.session_state.decisions, st.session_state.reviews, st.session_state.pending):
            store.pop(rec.source_file, None)
        st.rerun()


# --------------------------------------------------------------------------- #
# UI — pipeline ledger tab (reads the second tool live)
# --------------------------------------------------------------------------- #
def pipeline_ledger() -> None:
    ss = st.session_state
    st.subheader("Pipeline system of record — Azure Cosmos DB")
    st.caption("Every decided deal, read live from the `deals` container. This is the second tool.")

    rows = ss.pipeline.all_deals()
    top = st.columns([1, 1, 4])
    top[0].metric("Deals on record", len(rows))
    if top[1].button("🔄 Refresh"):
        st.rerun()

    if not rows:
        st.info("No deals recorded yet. Decide a deal in the Review tab and it appears here.")
        return

    table = []
    for r in rows:
        final = r.get("final") or {}
        recd = r.get("recommendation") or {}
        table.append({
            "company": r.get("company_name"),
            "recommended": recd.get("decision"),
            "final": final.get("decision"),
            "agreed": "✅" if final.get("agreed_with_ai") else "✏️",
            "reason": recd.get("reason_code"),
            "duplicate_of": (r.get("flags") or {}).get("duplicate_of"),
            "decided_at": (final.get("decided_at") or "")[:19].replace("T", " "),
            "source": r.get("source_file"),
        })
    st.dataframe(table, width="stretch", hide_index=True)

    with st.expander("Demo reset — clear the pipeline"):
        st.caption("Deletes every record in the Cosmos container. Use to re-run the duplicate demo cleanly.")
        if st.button("🗑️ Clear pipeline"):
            for r in rows:
                try:
                    ss.pipeline.container.delete_item(item=r["id"], partition_key=r["company_key"])
                except Exception:
                    pass
            ss.decisions.clear()
            st.rerun()


# --------------------------------------------------------------------------- #
def require_password() -> None:
    """Simple shared-password gate so only the panel can use the public app.

    The password is read from APP_PASSWORD (set it in the Streamlit Secrets store).
    If APP_PASSWORD is not set (e.g. local development), the app is open.
    """
    expected = os.getenv("APP_PASSWORD")
    if not expected or st.session_state.get("authed"):
        return
    st.title("Northbridge Deal Triage — sign in")
    pw = st.text_input("Enter the access password", type="password")
    if pw:
        if pw == expected:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()  # nothing else renders until the password is correct


def main() -> None:
    require_password()
    init_state()
    sidebar_queue()
    st.title("Northbridge Capital — Inbound Deal Triage")
    review_tab, ledger_tab = st.tabs(["🗂️ Review", "📊 Pipeline ledger"])
    with review_tab:
        detail_panel(st.session_state.selected)
    with ledger_tab:
        pipeline_ledger()


main()
