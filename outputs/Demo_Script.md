# Demo script — Northbridge Inbound Deal-Triage Agent (2–3 min)

A live, screen-recorded walkthrough. Goal: show the agent doing **real cognitive work**,
**two tools**, and a **human-in-the-loop** — not a slideshow. Decide deals in the order
below so the duplicate demo works (deal #1 must be recorded before #10 is opened).

**Before you hit record:** `streamlit run app.py`, open the app, and in the **Pipeline
ledger** tab click **Clear pipeline** so you start from an empty system of record.

---

### 0:00 — Setup (~15s)
- One line: "A growth-equity fund gets more inbound teasers than analysts can read. This
  agent triages each one against the fund's mandate, keeps the human on the decision, and
  logs every outcome to a pipeline system of record."
- Show the **deal queue** (sidebar, 11 deals) and the two tools you'll use: Azure AI Search
  (mandate) + Azure Cosmos DB (pipeline ledger).

### 0:15 — A clean Pass, instantly grounded (~25s)
- Open **#3 CoinSwift**. Agent returns **🔴 Pass / X-SECTOR**.
- Point at the rationale citing **§3.1** and the gate-by-gate panel. "It didn't just
  summarize — it judged the sector against the mandate and cited the rule."

### 0:40 — A clean Pursue → written to the pipeline (~30s)
- Open **#1 Meridian Ops**. Agent returns **🟢 Pursue / FIT-OK**, all gates green.
- Click **🟢 Pursue** (you, the human, make the call). "Only now does it write to Cosmos."
- Switch to the **Pipeline ledger** tab — the Meridian row appears live. That's tool 2.

### 1:10 — The duplicate catch (two tools + memory) (~30s)
- Back to the queue, open **#10 Meridian Ops (alt broker)** — same company, different banker.
- Agent returns **🟡 Needs-review / R-DUP** with **⚠️ Probable duplicate of
  01_meridian_ops_teaser**. "Before triaging, it checked the pipeline in Cosmos and
  recognized a company we've already seen — under a different broker."

### 1:40 — The "wow": a deck caught contradicting itself (~35s)
- Open **#11 Solari Robotics** (an image-only 5-page deck). Agent returns **🟡 Needs-review /
  R-INCONSISTENT**.
- Read the rationale: **"ARR is shown as $42.0M … and $37.2M …"**. Scroll the deck preview to
  page 2 and page 5 to show both numbers. "It read an image-only deck with vision and caught
  it disagreeing with itself — the kind of thing that's easy to miss at volume."

### 2:15 — The human override (where the AI stops) (~20s)
- Open any **Needs-review** deal (e.g. **#7 Halsted**, above the size band). Show that the
  agent recommends but does not decide; click **Pass** or **Hold** and add a note. "The AI
  never advances or declines — a person does, and the override is recorded."

### 2:35 — Close (~15s)
- Show the **Pipeline ledger** now populated: recommended vs. final, agreed vs. overridden.
- "Two tools, one human decision, and an auditable record of every call — shippable today."

---

### Backup / talking points if asked
- **What's deterministic vs. the model:** the model fills four semantic gates
  (sector/geo/stage/quality); code (`map_decision`) applies the mandate's §4 order. Reason
  codes are unit-tested (19/19).
- **What breaks at 10×:** model latency first (batch + concurrency); then name-based dedup
  (needs entity resolution).
- **Built in Claude Code**, on the fund's existing Azure stack (Agent Framework + AI Search
  + Cosmos + Streamlit).
