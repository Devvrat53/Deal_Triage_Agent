# Northbridge Inbound Deal-Triage Agent

An AI agent that does a **first-pass triage** of inbound deals (teasers, decks, memos)
against a fund's investment mandate, and hands an Investments team member a recommended
decision — **Pursue / Pass / Needs-review** — that they confirm or override. The human
always makes the final advance-to-committee call.

Built on **Microsoft Agent Framework + Microsoft Foundry + Azure AI Search + Azure Cosmos DB + Streamlit**.

---

## The big picture

One deal document flows through the pipeline:

```
deal document (PDF teaser / image-only deck)
   │
   ▼  (1) EXTRACT      read the document (text or vision)              →  DealFacts
   │
   ▼  (2) DEDUP-CHECK  ask Cosmos: have we seen this company before?   →  duplicate_of   [TOOL 2]
   │
   ▼  (3) RETRIEVE     search the mandate for the relevant rules       →  Passages       [TOOL 1]
   │
   ▼  (4) ADJUDICATE   model judges each "gate", code maps it to a decision  →  Adjudication
   │
   ▼  (5) PAUSE        workflow stops and shows the reviewer the recommendation
   │
   ▼  (6) RECORD       reviewer pursues / passes / holds → written to Cosmos   →  DealRecord   [TOOL 2]
```

Two tools, cleanly separated:
- **Azure AI Search** grounds the *judgment* — "what does the mandate say about this deal?"
- **Azure Cosmos DB** is the *system of record* — "what have we seen, and what did we decide?"
- **The human** is the gate between the AI's recommendation and the write to that record.

The key idea: **the model does the reading and judgment; plain Python makes the final
decision.** That keeps the decision rules predictable and testable.

---

## The files (in `src/`)

| File | What it does | Look here to change… |
|------|--------------|----------------------|
| `config.py` | Loads `.env` and builds the Azure clients (chat, embeddings, search, Cosmos). | endpoints, model/deployment names |
| `models.py` | The data shapes (`DealFacts`, `GateFindings`, `Adjudication`, `DealRecord`, reason codes). Pydantic. | the fields the model fills in, the reason-code list |
| `ingest.py` | One-time: chunk the mandate doc and load it into Azure AI Search. | how the mandate is chunked/indexed |
| `extraction.py` | Step 1: read a deal → `DealFacts` (text-native PDFs as text; image-only decks via vision). | how documents are read, new formats |
| `pipeline.py` | **Tool 2** — the Cosmos deal-pipeline: dedup read + decision write. | the deal record shape, dedup logic |
| `retrieval.py` | **Tool 1** — search the mandate index → `Passages`. | how/what we search the mandate for |
| `adjudicator.py` | The gate logic + the decision rules (`map_decision`). **The heart of it.** | the decision rules, thresholds, reason codes |
| `prompts.py` | All the prompt text (extraction + adjudication). | how the model is instructed |
| `workflow.py` | The human-in-the-loop workflow (dedup → adjudicate → pause → record). | the reviewer step, the two tool touch-points |
| `app.py` (root) | The Streamlit UI (deal queue + review + live pipeline ledger). | anything the reviewer sees/clicks |

`tests/test_adjudicator_mapping.py` checks the decision rules without calling the model.

---

## How a decision is actually made

The mandate defines a screening sequence (its §4 order). `adjudicator.map_decision()` runs
these in order and the **first step that decides wins**. Hard-exclude gates (sector /
geography / stage) run first, so an excluded-sector company is a clean Pass even if it's
also a duplicate.

| Step | Question | Outcome |
|------|----------|---------|
| Gate 1 | Hard-excluded sector? (§3.1) | Pass · **X-SECTOR** |
| Gate 2 | Headquartered outside US/Canada? (§3.2) | Pass · **X-GEO** |
| Gate 3 | Pre-revenue / seed stage? (§3.3) | Pass · **X-STAGE** |
| pre-check | Revenue & growth present? (§5) | Needs-review · **R-INCOMPLETE** |
| Gate 4 | Revenue in the $10M–$100M band? (§3.4) | below → Pass **X-SIZE-LOW** · above → Needs-review **R-SIZE-HIGH** |
| Integrity | Do the document's own figures agree? (§7) | Needs-review · **R-INCONSISTENT** |
| Pipeline | Already in the pipeline? (§6) | Needs-review · **R-DUP** |
| Gate 5 | Meets the quality bar (recurring / growth / NRR, incl. trend)? (§3.5) | Needs-review · **R-QUALITY** |
| else | — | Pursue · **FIT-OK** |

The model only fills in `GateFindings` (its judgment on the four *semantic* gates: sector /
geography / stage / quality). The numeric checks (size, completeness), the figure-consistency
flag (from the reader), and the duplicate signal (from Cosmos) are combined in code. The
mapping above is code.

---

## Running it

```bash
# 1. one-time: load the mandate into Azure AI Search
PYTHONPATH=. python -m src.ingest

# 2. the decision-rules test (no API calls)
PYTHONPATH=. python -m tests.test_adjudicator_mapping

# 3. triage one deal on the command line (shows the pause/resume)
PYTHONPATH=. python -m src.workflow data/deals/03_coinswift_teaser.pdf

# 4. the app
streamlit run app.py
```

Settings live in `.env` (`AZURE_OPENAI_*`, `AZURE_SEARCH_*`, `AZURE_COSMOS_*`).
