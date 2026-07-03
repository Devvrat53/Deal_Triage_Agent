"""Shared data contract for the whole pipeline.

These schemas are the single source of truth for the shapes that flow between
extraction -> adjudicator -> HITL workflow -> Streamlit UI -> Cosmos. Keeping them
in one place is what stops those modules from drifting apart.

We use Pydantic models because the Agent Framework can use them directly as a
structured-output ``response_format`` (the model returns a typed object on
``response.value``). The ``Field`` descriptions below double as instructions to the
model, so they are written to be read by it.
"""

from enum import Enum
from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Enums — the triage vocabulary (mirror the mandate's outcomes)
# --------------------------------------------------------------------------- #
class Decision(str, Enum):
    """The agent's recommendation; a human Investments team member makes the final call."""
    PURSUE = "Pursue"              # clears the mandate -> advance to the team for diligence
    PASS = "Pass"                  # fails a hard-exclude gate -> decline
    NEEDS_REVIEW = "Needs-review"  # can't be resolved by the gates alone -> human judgment


class ReasonCode(str, Enum):
    """Why a deal landed where it did (mandate §4 decision sequence)."""
    FIT_OK = "FIT-OK"              # Pursue — clears every gate
    X_SECTOR = "X-SECTOR"          # Pass — hard-excluded sector (§3.1)
    X_GEO = "X-GEO"                # Pass — headquartered outside US/Canada (§3.2)
    X_STAGE = "X-STAGE"            # Pass — pre-revenue / seed stage (§3.3)
    X_SIZE_LOW = "X-SIZE-LOW"      # Pass — below the $10M revenue floor (§3.4)
    R_SIZE_HIGH = "R-SIZE-HIGH"    # Needs-review — above the $100M band, strategy-fit call (§3.4)
    R_QUALITY = "R-QUALITY"        # Needs-review — soft / declining quality metrics (§3.5)
    R_INCOMPLETE = "R-INCOMPLETE"  # Needs-review — key financials missing, can't screen (§5)
    R_DUP = "R-DUP"                # Needs-review — probable duplicate in the pipeline (§6)
    R_INCONSISTENT = "R-INCONSISTENT"  # Needs-review — internally inconsistent figures (§7)


class DocumentType(str, Enum):
    TEASER = "teaser"          # short intro one/two-pager from a banker
    DECK = "deck"              # a pitch / management presentation
    CIM = "cim"                # confidential information memorandum
    ONE_PAGER = "one_pager"
    EMAIL = "email"
    OTHER = "other"


# --------------------------------------------------------------------------- #
# Extraction output — observable facts only (no mandate judgment here)
# --------------------------------------------------------------------------- #
class DealFacts(BaseModel):
    """What the agent can *read* off a single deal document.

    Deliberately limited to observable facts. Whether the sector is in mandate, the
    geography qualifies, the size fits, etc. is the adjudicator's job, not the
    reader's — that separation keeps the reasoning auditable. Anything not stated in
    the document should be left null rather than guessed.
    """

    company_name: str | None = Field(None, description="The company being pitched")
    broker: str | None = Field(None, description="The bank / advisor who prepared the document, if named")
    received_date: str | None = Field(None, description="Date on the document (e.g. 'March 2026'), verbatim")

    sector: str | None = Field(None, description="The company's business / sector as described, verbatim (e.g. 'fintech infrastructure', 'consumer crypto exchange')")
    hq_location: str | None = Field(None, description="Headquarters location as stated, verbatim (e.g. 'Austin, TX, USA', 'Berlin, Germany')")
    stage: str | None = Field(None, description="Company stage as described (e.g. 'growth', 'pre-revenue seed'); null if not stated")
    deal_type: str | None = Field(None, description="Type/structure of the deal if stated (e.g. 'minority growth equity', 'majority buyout')")

    revenue: float | None = Field(None, description="Trailing revenue or ARR, in MILLIONS (e.g. 30.4 for $30.4M). Null if not disclosed.")
    revenue_type: str | None = Field(None, description="What the revenue figure represents, verbatim (e.g. 'ARR', 'revenue', 'trading fee revenue')")
    revenue_currency: str | None = Field(None, description="Currency of the revenue/ask figures, e.g. USD/CAD/EUR")
    yoy_growth_pct: float | None = Field(None, description="Year-over-year growth rate as a number (e.g. 41 for 41%). Null if not disclosed.")
    recurring_pct: float | None = Field(None, description="Recurring revenue as a percent of total (e.g. 94 for 94%). Null if not stated.")
    nrr_pct: float | None = Field(None, description="Net revenue retention as a number (e.g. 118 for 118%). Null if not stated.")
    gross_margin_pct: float | None = Field(None, description="Gross margin percent. Null if not stated.")
    ebitda_margin_pct: float | None = Field(None, description="EBITDA margin percent (can be negative). Null if not stated.")
    ask_amount: float | None = Field(None, description="Capital the company is raising, in MILLIONS. Null if not stated.")

    document_type: DocumentType = Field(DocumentType.OTHER, description="What kind of document this is")

    # Reading-time integrity signal — belongs to the reader because catching it
    # requires seeing the whole document (e.g. ARR on page 2 vs page 5).
    figures_consistent: bool | None = Field(None, description="False if the SAME metric is stated with materially different values in different parts of the document (e.g. ARR $42M on one page, $37.2M on another). True if consistent. Null if only one value is given / not assessable.")

    observations: list[str] = Field(default_factory=list, description="Anything noteworthy for the reviewer: missing financials, figures that disagree across pages (quote the conflicting values), unusual deal structure, etc.")
    extraction_confidence: float = Field(0.0, ge=0.0, le=1.0, description="0-1 confidence the key fields above were read correctly. Lower for image-only decks / sparse teasers.")


# --------------------------------------------------------------------------- #
# Adjudication — the LLM's per-gate judgments + the final recommendation
# --------------------------------------------------------------------------- #
class GateFindings(BaseModel):
    """What the LLM judges per mandate gate, grounded in retrieved mandate text.

    The LLM fills this in for the *semantic* gates (does this sector/geography/stage/
    quality fit the mandate?). Deterministic Python (adjudicator.map_decision) then
    combines these with the numeric checks (size band, completeness, duplicate,
    figure-consistency) to produce the final Decision + ReasonCode, following the
    §4 sequence. Keeping the *policy* in code (not the LLM) is what makes the
    reason-code mapping faithful and testable.

    Tri-state fields use None to mean "cannot determine from the document."
    """

    # Gate 1 — Sector (§3.1)
    mapped_sector: str | None = Field(None, description="Which mandate sector this maps to (B2B SaaS / tech-enabled services / fintech infrastructure / healthcare IT), or the excluded category it falls under, or null if unclear")
    sector_hard_excluded: bool | None = Field(description="True if the company's PRIMARY business is on the §3.1 hard-exclude list (consumer/D2C, crypto/digital assets, gaming, real estate, energy/cleantech, defense). False if it's an in-mandate or otherwise permitted sector. None if genuinely unclear.")
    sector_note: str = Field(description="One line, cite §3.1")

    # Gate 2 — Geography (§3.2)
    geography_in_mandate: bool | None = Field(description="True if the company is HEADQUARTERED / primarily operates in the US or Canada. False if headquartered outside North America. None if location isn't stated. (Customer or target-market location does NOT count.)")
    geography_note: str = Field(description="One line, cite §3.2")

    # Gate 3 — Stage (§3.3)
    stage_post_revenue: bool | None = Field(description="True if the company is post-revenue / growth stage. False if pre-revenue or seed stage. None if stage can't be determined.")
    stage_note: str = Field(description="One line, cite §3.3")

    # Gate 5 — Business quality (§3.5) — judgment incl. trend, not just the point value
    quality_meets_bar: bool | None = Field(description="True if it clears all three of recurring>70%, YoY growth>20%, NRR>100%. False if it clearly misses one or more. None if the metrics needed aren't disclosed. Weigh whether the trend is improving or deteriorating (§3.5 says trend matters more than the single-point number).")
    quality_note: str = Field(description="One line noting which metric(s) miss and the direction of travel; cite §3.5")

    citations: list[str] = Field(default_factory=list, description="Mandate sections relied on, e.g. 'Investment Mandate §3.1'")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="0-1 overall confidence in these findings")


class GateResult(BaseModel):
    """One gate's outcome, for display in the recommendation."""
    name: str = Field(description="Gate name, e.g. 'Gate 1 — Sector'")
    passed: bool | None = Field(None, description="True=cleared, False=failed, None=cannot determine")
    note: str = Field(description="One-line justification, citing the mandate where possible")


class Adjudication(BaseModel):
    """The full recommendation the human reviewer sees."""
    decision: Decision
    reason_code: ReasonCode
    rationale: str = Field(description="Reviewer-facing explanation of why this decision was reached")
    gates: list[GateResult] = Field(default_factory=list, description="Per-gate results in order")
    citations: list[str] = Field(default_factory=list, description="Mandate sections relied on")
    duplicate_of: str | None = Field(None, description="If flagged as a probable duplicate, the id/source of the earlier pipeline submission")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="0-1 overall confidence in this recommendation")
    facts: DealFacts | None = Field(None, description="The deal facts this decision was based on")


# --------------------------------------------------------------------------- #
# Human-in-the-loop — the one decision that stays human
# --------------------------------------------------------------------------- #
class AnalystActionType(str, Enum):
    """What the Investments team member can decide in the review step."""
    PURSUE = "pursue"          # advance to the team / IC for diligence
    PASS = "pass"              # decline
    HOLD = "hold"              # keep in the pipeline for review / request more info


class AnalystAction(BaseModel):
    """The reviewer's decision sent back into the paused workflow."""
    action: AnalystActionType
    note: str = Field("", description="Override reason, or what to request from the broker; required when overriding the AI")


# --------------------------------------------------------------------------- #
# Deal record — the shape persisted to Cosmos (the pipeline system of record)
# --------------------------------------------------------------------------- #
class DealFlags(BaseModel):
    """Integrity signals the pipeline computed for this deal."""
    duplicate_of: str | None = Field(None, description="id of an earlier submission of the same company, if any")
    inconsistent_figures: bool = Field(False, description="True if the document contradicted itself on a key metric")


class FinalOutcome(BaseModel):
    """The human's decision block — only written after the reviewer acts."""
    decision: Decision
    agreed_with_ai: bool = Field(description="True if the reviewer followed the agent's recommendation")
    decided_by: str = Field("analyst", description="Who made the call")
    note: str = ""
    decided_at: str | None = Field(None, description="ISO timestamp of the decision")


class DealRecord(BaseModel):
    """One deal submission in the pipeline. ``model_dump()`` of this is exactly the
    JSON document stored in Cosmos, so ``id`` and ``company_key`` (the partition key)
    live at the top level.

    ``status`` is "recommended" once the AI has produced a recommendation and is
    awaiting the human, and "decided" once ``final`` is filled in.
    """
    id: str = Field(description="Unique per submission (e.g. the source filename without extension)")
    company_key: str = Field(description="Normalized company name — partition + dedup key (see pipeline.normalize_company)")
    company_name: str | None = None
    source_file: str | None = None
    broker: str | None = None
    received_date: str | None = None

    facts: DealFacts | None = None
    recommendation: Adjudication | None = None
    flags: DealFlags = Field(default_factory=DealFlags)
    final: FinalOutcome | None = None
    status: str = "recommended"
