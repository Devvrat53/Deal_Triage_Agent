"""Searches the mandate index in Azure AI Search.

Given a question, it runs hybrid search (vector + keyword + semantic ranking) and
returns the most relevant mandate sections, each with a citation string.
"""

from dataclasses import dataclass
from azure.search.documents.models import VectorizedQuery
from . import config


@dataclass
class Passage:
    source_label: str    # "Investment Mandate"
    section: str         # heading path
    content: str         # the section text
    score: float

    @property
    def citation(self):
        return f"{self.source_label} — {self.section}"


class Retriever:
    """Build this once and reuse it — it holds the embedding and search clients."""

    def __init__(self):
        self.embed_client = config.build_embedding_client()
        self.search_client = config.build_search_client()

    async def search(self, query, top=5):
        """Run one hybrid search and return the top passages."""
        vector = (await self.embed_client.get_embeddings([query]))[0].vector
        results = self.search_client.search(
            search_text=query,                                  # keyword search
            vector_queries=[VectorizedQuery(vector=vector, k=30, fields="content_vector")],  # vector search k_nearest_neighbors -> k
            query_type="semantic",                              # rerank the combined results
            semantic_configuration_name="semantic-config",
            select=["content", "section", "source_label"],
            top=top,
        )
        passages = []
        for r in results:
            passages.append(Passage(
                source_label=r["source_label"],
                section=r["section"],
                content=r["content"],
                score=r.get("@search.reranker_score") or r["@search.score"],
            ))
        return passages

    async def grounding_for(self, queries, total=8):
        """Search several questions, then merge the results (best score per section).

        We ask a few targeted questions (is the sector in mandate? the geography?
        the size band?) instead of one vague one, so the adjudicator always sees the
        rules it needs.
        """
        best = {}
        for query in queries:
            for passage in await self.search(query, top=3):
                if passage.section not in best or passage.score > best[passage.section].score:
                    best[passage.section] = passage
        passages = sorted(best.values(), key=lambda p: p.score, reverse=True)
        return passages[:total]
