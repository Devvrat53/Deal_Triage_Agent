"""The deal-pipeline system of record, backed by Azure Cosmos DB.

This is the second tool (alongside Azure AI Search). It gives the agent a memory
of every deal it has seen:

  * READ  — before a deal is judged, we check whether the same company is already
            in the pipeline (mandate §6, "Pipeline Deduplication"). A match forces
            the deal to Needs-review instead of being screened as brand new.
  * WRITE — after a human confirms or overrides the recommendation, we persist the
            final decision. The write happens on the *human's* action, not the AI's.

Records are plain dicts (Cosmos documents are JSON), so this layer doesn't depend
on the pipeline's Pydantic models — the workflow serializes a deal to a dict before
handing it here.
"""

import re
from azure.cosmos import PartitionKey, exceptions
from . import config


def normalize_company(name):
    """Turn a display name into the dedup / partition key.

    "Meridian Ops", "meridian ops", "Meridian  Ops " all map to "meridian ops",
    so the same company shopped by two brokers lands in the same partition.
    """
    return re.sub(r"\s+", " ", (name or "").strip().lower())


class DealPipeline:
    """Build this once and reuse it — it holds the Cosmos client and container.

    Creating it also creates the database and container on first run (serverless,
    partitioned by /company_key), so there's no separate setup step.
    """

    def __init__(self):
        client = config.build_cosmos_client()
        db = client.create_database_if_not_exists(id=config.COSMOS_DATABASE)
        self.container = db.create_container_if_not_exists(
            id=config.COSMOS_CONTAINER,
            partition_key=PartitionKey(path=config.COSMOS_PARTITION_KEY),
        )

    def find_duplicates(self, company_key, exclude_id=None):
        """Return existing pipeline records for this company (a single-partition read).

        Pass the current submission's own id as exclude_id so re-recording a deal
        doesn't flag it as a duplicate of itself.
        """
        rows = list(self.container.query_items(
            query="SELECT * FROM c WHERE c.company_key = @k",
            parameters=[{"name": "@k", "value": company_key}],
            partition_key=company_key,
        ))
        if exclude_id is not None:
            rows = [r for r in rows if r.get("id") != exclude_id]
        return rows

    def record_decision(self, deal):
        """Persist a deal record (upsert). Called after the human confirms/overrides.

        `deal` is a dict; it must carry a unique `id` and a non-empty `company_key`
        (use normalize_company for the latter).
        """
        if not deal.get("company_key"):
            raise ValueError("deal needs a non-empty 'company_key' (use normalize_company)")
        if not deal.get("id"):
            raise ValueError("deal needs a unique 'id'")
        return self.container.upsert_item(deal)

    def get_deal(self, deal_id, company_key):
        """Fetch one record by id, or None if it isn't there."""
        try:
            return self.container.read_item(item=deal_id, partition_key=company_key)
        except exceptions.CosmosResourceNotFoundError:
            return None

    def all_deals(self):
        """Every record in the pipeline, newest first — used for the ledger view."""
        rows = list(self.container.query_items(
            query="SELECT * FROM c",
            enable_cross_partition_query=True,
        ))
        return sorted(rows, key=lambda r: r.get("_ts", 0), reverse=True)
