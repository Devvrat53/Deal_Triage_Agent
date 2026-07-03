"""All Azure settings and clients live here, so the rest of the code never
reads environment variables directly. Import what you need from this file.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from agent_framework.openai import OpenAIChatClient, OpenAIEmbeddingClient
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.cosmos import CosmosClient, PartitionKey

# Load .env from the project root explicitly. load_dotenv() with no argument
# searches from the *calling file's* directory, which breaks when a script
# outside src/ imports this module — so we pin it to the repo root here.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)  # the Azure SDKs don't read .env on their own, so we do it here

# --- Azure OpenAI ---
# The endpoint already ends in /openai/v1/, so we pass it as base_url and let the
# OpenAI SDK pick the right api-version.
AOAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AOAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-5.4-mini")
EMBED_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-ada-002")
EMBED_DIMENSIONS = 1536  # text-embedding-ada-002 returns 1536-long vectors

# --- Azure AI Search (grounds the mandate-fit judgement) ---
SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
SEARCH_API_KEY = os.getenv("AZURE_SEARCH_API_KEY")
SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "northbridge-mandate")

# --- Azure Cosmos DB (the deal-pipeline system of record) ---
COSMOS_ENDPOINT = os.getenv("AZURE_COSMOS_ENDPOINT")
COSMOS_KEY = os.getenv("AZURE_COSMOS_KEY")
COSMOS_DATABASE = os.getenv("AZURE_COSMOS_DATABASE", "northbridge")
COSMOS_CONTAINER = os.getenv("AZURE_COSMOS_CONTAINER", "deals")
COSMOS_PARTITION_KEY = "/company_key"  # deals are partitioned by normalized company name


def build_chat_client():
    """The chat model (gpt-5.4-mini). model = the Azure deployment name."""
    return OpenAIChatClient(model=CHAT_DEPLOYMENT, base_url=AOAI_ENDPOINT, api_key=AOAI_API_KEY)


def build_embedding_client():
    """The embedding model, used to turn text into vectors for search."""
    return OpenAIEmbeddingClient(model=EMBED_DEPLOYMENT, base_url=AOAI_ENDPOINT, api_key=AOAI_API_KEY)


def build_index_client():
    """Admin client — used once during ingestion to create the search index."""
    return SearchIndexClient(endpoint=SEARCH_ENDPOINT, credential=AzureKeyCredential(SEARCH_API_KEY))


def build_search_client():
    """Client used to upload chunks and run searches against the index."""
    return SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX,
        credential=AzureKeyCredential(SEARCH_API_KEY),
    )


def build_cosmos_client():
    """Client for the deal-pipeline system of record (Azure Cosmos DB, NoSQL API)."""
    return CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
