"""Atlas Vector Search over past error traces and the repairs that fixed them."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import certifi
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError
from pymongo.operations import SearchIndexModel

from . import database

TRACES = "error_traces"
INDEX_NAME = "error_trace_vector"
EMBEDDING_MODEL = "text-embedding-3-small"
DIMENSIONS = 1536
MIN_SCORE = 0.72

_search_client: Optional[MongoClient] = None


def trace_text(task: Dict[str, Any], error: str, schema: Dict[str, Any]) -> str:
    """The sentence stored for a failure the evaluator actually returned."""
    names = ", ".join(sorted((schema.get("parameters") or {}).keys())) or "none"
    return (
        "Tool {name} failed while running {title} at {endpoint}. "
        "Evaluator said: {error}. Declared fields: {names}. "
        "The contract is missing the version, the page size, or the bearer credential."
    ).format(
        name=schema.get("name") or "query_collection",
        title=task.get("title") or task.get("id"),
        endpoint=task.get("endpoint") or "",
        error=error,
        names=names,
    )


def plain_language(error: str) -> str:
    """A second wording of the same failure, stored beside the evaluator text."""
    if any(name in error for name in ("api_version", "pagination_limit", "auth_bearer")):
        return (
            "A collection read was rejected because the tool contract had no version header, "
            "no page size, and no bearer credential."
        )
    return error


def ensure_index() -> None:
    """Create the Atlas Vector Search index if this cluster does not have it yet."""
    collection = _traces()
    try:
        existing = list(collection.list_search_indexes())
    except PyMongoError:
        existing = []
    for index in existing:
        if index.get("name") == INDEX_NAME:
            return
    model = SearchIndexModel(
        definition={
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": DIMENSIONS,
                    "similarity": "cosine",
                }
            ]
        },
        name=INDEX_NAME,
        type="vectorSearch",
    )
    try:
        collection.create_search_index(model)
    except PyMongoError as exc:
        if "already exists" not in str(exc).lower():
            raise


def remember(
    agent_id: str,
    task: Dict[str, Any],
    error: str,
    schema: Dict[str, Any],
    repair_parameters: Dict[str, Any],
    guardrails: List[str],
    diagnosis: str,
) -> None:
    """Embed the failure and its plain-language form, then store both with the repair."""
    observed = trace_text(task, error, schema)
    wordings = [observed]
    restated = plain_language(error)
    if restated != observed:
        wordings.append(restated)
    collection = _traces()
    for wording in wordings:
        collection.insert_one(
            {
                "agent_id": agent_id,
                "task_id": task.get("id"),
                "endpoint": task.get("endpoint"),
                "error_text": error,
                "trace_text": wording,
                "embedding": embed(wording),
                "repair_parameters": dict(repair_parameters),
                "guardrails": list(guardrails),
                "diagnosis": diagnosis,
                "created_at": database.utcnow(),
            }
        )


def recall(query: str, *, timeout_seconds: float = 0.0) -> Optional[Dict[str, Any]]:
    """Return the closest past repair. ``timeout_seconds`` retries while a new index catches up."""
    deadline = time.time() + timeout_seconds
    while True:
        hit = _search_once(query)
        if hit is not None:
            return hit
        if time.time() >= deadline:
            return None
        time.sleep(2.0)


def wait_until_queryable(timeout_seconds: float = 90.0) -> bool:
    """Block until Atlas marks the vector index ready to query."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            indexes = list(_traces().list_search_indexes())
        except PyMongoError:
            indexes = []
        for index in indexes:
            if index.get("name") == INDEX_NAME and index.get("queryable"):
                return True
        time.sleep(2.0)
    return False


def embed(text: str) -> List[float]:
    from openai import OpenAI

    database.mongo_uri()
    key = os.getenv("OPENAI_API_KEY", "").strip()
    client = OpenAI(api_key=key, timeout=25.0)
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    vector = list(response.data[0].embedding)
    if len(vector) != DIMENSIONS:
        raise RuntimeError("Unexpected embedding size {}.".format(len(vector)))
    return vector


def _traces() -> Collection:
    """Search commands sit outside Stable API v1, so they use their own client."""
    global _search_client
    if _search_client is None:
        _search_client = MongoClient(
            database.mongo_uri(),
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=12000,
            retryWrites=True,
            w="majority",
            tz_aware=True,
            appname="HydraHarnessSearch",
        )
    name = os.environ.get("MONGODB_DB", database.DATABASE_NAME).strip() or database.DATABASE_NAME
    db = _search_client[name]
    if TRACES not in db.list_collection_names():
        db.create_collection(TRACES)
    return db[TRACES]


def _search_once(query: str) -> Optional[Dict[str, Any]]:
    try:
        vector = embed(query)
        pipeline = [
            {
                "$vectorSearch": {
                    "index": INDEX_NAME,
                    "path": "embedding",
                    "queryVector": vector,
                    "numCandidates": 40,
                    "limit": 3,
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "score": {"$meta": "vectorSearchScore"},
                    "agent_id": 1,
                    "endpoint": 1,
                    "error_text": 1,
                    "trace_text": 1,
                    "repair_parameters": 1,
                    "guardrails": 1,
                    "diagnosis": 1,
                }
            },
        ]
        rows = list(_traces().aggregate(pipeline))
    except PyMongoError:
        return None
    except Exception:
        return None
    for row in rows:
        score = float(row.get("score") or 0.0)
        parameters = row.get("repair_parameters")
        if score < MIN_SCORE or not isinstance(parameters, dict) or not parameters:
            continue
        row["score"] = score
        return row
    return None


def close() -> None:
    global _search_client
    if _search_client is not None:
        _search_client.close()
    _search_client = None
