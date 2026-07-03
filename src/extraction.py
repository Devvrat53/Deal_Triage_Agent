"""Step 3 — read a single deal document and pull out the deal facts.

The model reads the document and returns a DealFacts object.
 - images (.jpg/.png) are sent straight to the model
 - text documents (.txt) are sent as text
 - PDFs: if the PDF has a real text layer (our banker teasers) we send the text;
   if it's an image-only deck (our scanned/rendered decks) we turn each page into a
   PNG and let the vision model read it. Sending a PDF as application/pdf is not
   supported by this API, so we never do that.

Usage:  facts = await extract_deal_facts("data/deals/02_ledgerline_teaser.pdf")
"""

from pathlib import Path
from agent_framework import Agent, Content, Message
from . import config, prompts
from .models import DealFacts

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

# If a PDF yields at least this many characters of text, treat it as text-native
# and skip the (slower, lossier) vision path.
TEXT_LAYER_MIN_CHARS = 50


def build_extract_agent():
    """The agent that reads deal documents. Build once and reuse."""
    return Agent(client=config.build_chat_client(), instructions=prompts.EXTRACTION_SYSTEM)


def pdf_text(path):
    """Return all extractable text from the PDF (empty-ish for image-only decks)."""
    import pymupdf

    with pymupdf.open(str(path)) as doc:
        return "\n\n".join(page.get_text() for page in doc).strip()


def pdf_to_images(path):
    """Turn each PDF page into PNG bytes (for image-only decks)."""
    import pymupdf

    images = []
    with pymupdf.open(str(path)) as doc:
        for page in doc:
            images.append(page.get_pixmap(dpi=200).tobytes("png"))
    return images


def build_message(path):
    """Build the chat message we send the model: a prompt plus the deal document."""
    suffix = path.suffix.lower()
    parts = [Content.from_text(prompts.EXTRACTION_USER)]

    if suffix in IMAGE_TYPES:
        parts.append(Content.from_data(data=path.read_bytes(), media_type=IMAGE_TYPES[suffix]))
    elif suffix == ".txt":
        text = path.read_text(encoding="utf-8", errors="replace")
        parts.append(Content.from_text(f"Deal document content:\n\n{text}"))
    elif suffix == ".pdf":
        text = pdf_text(path)
        if len(text) >= TEXT_LAYER_MIN_CHARS:
            parts.append(Content.from_text(f"Deal document content:\n\n{text}"))
        else:
            # image-only deck — send every page so the model can compare across pages
            for png in pdf_to_images(path):
                parts.append(Content.from_data(data=png, media_type="image/png"))
    else:
        raise ValueError(f"Unsupported deal document format: {suffix}")

    return Message(role="user", contents=parts)


async def extract_deal_facts(path, agent=None):
    """Read one deal document and return a DealFacts object."""
    path = Path(path)
    agent = agent or build_extract_agent()
    # response_format=DealFacts makes the model return our exact schema as response.value
    response = await agent.run(build_message(path), options={"response_format": DealFacts})
    return response.value


# Small command-line helper: `python -m src.extraction <deal>` (or no arg for all).
async def _main(argv):
    deals_dir = Path("data/deals")
    if argv:
        targets = [Path(argv[0])]
    else:
        targets = sorted(p for p in deals_dir.iterdir() if p.suffix.lower() in {*IMAGE_TYPES, ".pdf", ".txt"})

    agent = build_extract_agent()
    for path in targets:
        facts = await extract_deal_facts(path, agent=agent)
        rev = f"{facts.revenue}M {facts.revenue_type or ''}".strip() if facts.revenue is not None else "n/a"
        print(f"\n=== {path.name} ===")
        print(f"  company     : {facts.company_name}")
        print(f"  sector      : {facts.sector}")
        print(f"  hq          : {facts.hq_location}")
        print(f"  stage       : {facts.stage}")
        print(f"  revenue     : {rev}    growth: {facts.yoy_growth_pct}    NRR: {facts.nrr_pct}")
        print(f"  ask         : {facts.ask_amount}    doc type: {facts.document_type.value}")
        print(f"  figures_consistent: {facts.figures_consistent}    confidence: {facts.extraction_confidence}")
        if facts.observations:
            print(f"  observations: {facts.observations}")


if __name__ == "__main__":
    import asyncio
    import sys

    asyncio.run(_main(sys.argv[1:]))
