"""Step 2 — load the investment mandate into Azure AI Search (run once).

What it does:  read the mandate .docx -> split into one chunk per section ->
turn each chunk into a vector -> create the search index -> upload the chunks.

Run it with:  python -m src.ingest
"""

import asyncio
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from docx import Document
from . import config

MANDATE_DIR = Path("data/mandate")

# The fund runs a single strategy, so there's one mandate doc (unlike the WSA
# case's two manuals). One source, so no authority ranking to worry about.
MANDATE_FILE = "Northbridge_Capital_Partners_Investment_Mandate.docx"
SOURCE_LABEL = "Investment Mandate"


@dataclass
class Chunk:
    id: str
    source_label: str    # "Investment Mandate"
    section: str         # heading path, e.g. "3. Mandate Criteria > 3.1 Sector Focus"
    heading: str         # the section's own heading
    content: str         # heading path + text (this is what we search/embed)


def read_sections(path):
    """Walk the .docx and return a list of (section_path, heading, text) per section."""
    doc = Document(str(path))
    sections = []
    # Remember the current heading at each level so we can build a path like "3 > 3.1".
    current = {1: "", 2: "", 3: ""}
    heading = "(intro)"
    text_lines = []

    def save():
        text = "\n".join(line for line in text_lines if line.strip())
        if text.strip():
            path_parts = [current[lvl] for lvl in (1, 2, 3) if current[lvl]]
            sections.append((" > ".join(path_parts) or heading, heading, text))

    for para in doc.paragraphs:
        text = (para.text or "").strip()
        if not text:
            continue
        style = para.style.name if para.style else ""
        match = re.match(r"Heading (\d)", style)
        if match:
            save()                       # finish the previous section
            text_lines = []
            level = int(match.group(1))
            current[level] = text
            for deeper in range(level + 1, 4):  # a new heading clears the deeper ones
                current[deeper] = ""
            heading = text
        else:
            text_lines.append(text)
    save()  # don't forget the last section
    return sections


def load_chunks():
    """Turn the mandate doc into a flat list of Chunk objects."""
    chunks = []
    sections = read_sections(MANDATE_DIR / MANDATE_FILE)
    for i, (section_path, heading, text) in enumerate(sections):
        # Put the heading path inside the content so the search text is self-describing.
        content = f"[{SOURCE_LABEL} — {section_path}]\n{text}"
        chunks.append(Chunk(
            id=f"mandate-{i}",
            source_label=SOURCE_LABEL,
            section=section_path,
            heading=heading,
            content=content,
        ))
    return chunks


async def embed_chunks(chunks):
    """Turn each chunk's text into a vector using the embedding model."""
    client = config.build_embedding_client()
    result = await client.get_embeddings([c.content for c in chunks])
    return [e.vector for e in result]


def create_index():
    """Create (or replace) the search index with vector + keyword + semantic search."""
    from azure.search.documents.indexes.models import (
        SearchIndex, SearchField, SearchFieldDataType, SimpleField, SearchableField,
        VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile,
        SemanticConfiguration, SemanticPrioritizedFields, SemanticField, SemanticSearch,
    )

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchableField(name="heading", type=SearchFieldDataType.String),
        SearchableField(name="section", type=SearchFieldDataType.String),
        SimpleField(name="source_label", type=SearchFieldDataType.String, filterable=True),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=config.EMBED_DIMENSIONS,
            vector_search_profile_name="hnsw-profile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw-config")],
        profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw-config")],
    )

    semantic = SemanticSearch(
        default_configuration_name="semantic-config",
        configurations=[SemanticConfiguration(
            name="semantic-config",
            prioritized_fields=SemanticPrioritizedFields(
                title_field=SemanticField(field_name="heading"),
                content_fields=[SemanticField(field_name="content")],
                keywords_fields=[SemanticField(field_name="section")],
            ),
        )],
    )

    index = SearchIndex(
        name=config.SEARCH_INDEX, fields=fields,
        vector_search=vector_search, semantic_search=semantic,
    )

    client = config.build_index_client()
    if config.SEARCH_INDEX in [i.name for i in client.list_indexes()]:
        print(f"  index '{config.SEARCH_INDEX}' already exists — replacing it")
        client.delete_index(config.SEARCH_INDEX)
    client.create_index(index)
    print(f"  created index '{config.SEARCH_INDEX}'")


def upload(chunks, vectors):
    """Send the chunks (with their vectors) to the search index."""
    client = config.build_search_client()
    docs = []
    for chunk, vector in zip(chunks, vectors):
        doc = asdict(chunk)
        doc["content_vector"] = vector
        docs.append(doc)
    result = client.upload_documents(documents=docs)
    uploaded = sum(1 for r in result if r.succeeded)
    print(f"  uploaded {uploaded}/{len(docs)} documents")


async def main():
    print("1/4  reading + chunking the mandate...")
    chunks = load_chunks()
    print(f"     {len(chunks)} chunks")

    print("2/4  embedding chunks...")
    vectors = await embed_chunks(chunks)

    print("3/4  creating the search index...")
    create_index()

    print("4/4  uploading chunks...")
    upload(chunks, vectors)
    print("\nDone. Index:", config.SEARCH_INDEX)


if __name__ == "__main__":
    asyncio.run(main())
