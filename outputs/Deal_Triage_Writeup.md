# Northbridge Inbound Deal-Triage Agent

A growth-equity fund receives far more inbound teasers than analysts can carefully read.
Most are quick "no"s — wrong sector, geography, stage, or size — but they still consume
attention, and the occasional trap slips through: a deck whose numbers contradict
themselves, or a company already in the pipeline under a different banker.

**What the human can now do.** An Investments team member triages a stack of inbound deals
in minutes instead of an afternoon. Each deal arrives pre-read, screened against the mandate
with citations, cross-checked against the pipeline, and sorted into Pursue / Pass /
Needs-review — so their attention goes to judgment calls, not filtering.

**What the AI owns.** Reading each document (including image-only decks, via vision);
extracting the facts; catching internal contradictions (ARR on page 2 ≠ page 5); grounding
every gate in the mandate (Azure AI Search); checking the pipeline for duplicates (Azure
Cosmos DB); and recommending a decision with per-gate reasoning.

**Where the AI stops.** It never advances or declines a deal. Every recommendation pauses
for a human, who confirms or overrides; only then is the decision written to the pipeline
record. The decision rules live in code, not the model.

**What breaks first at 10× volume.** Model latency (~30s/deal, serial) — fixable with batched,
concurrent extraction. Then duplicate detection: matching on a normalized company name is
brittle, so real scale needs entity resolution — a company shopped under a subtly different
name would slip the check.
