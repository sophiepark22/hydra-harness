"""MongoDB Atlas state for Hydra-Harness agent versions."""

from __future__ import annotations

import copy
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import certifi
from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.server_api import ServerApi

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
DATABASE_NAME = "hydra_harness"
COLLECTION_NAME = "agent_states"

_client: Optional[MongoClient] = None
_database: Optional[Database] = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def mask_uri(uri: str) -> str:
    """Hide a password if a connection string is ever printed."""
    return re.sub(r"//([^:/]+):([^@]+)@", r"//\1:***@", uri)


def atlas_host(uri: str) -> str:
    if "@" not in uri:
        return "atlas"
    return uri.split("@", 1)[1].split("/", 1)[0].split("?", 1)[0]


def mongo_uri() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv()
    for name in ("MONGO_URI", "MONGODB_URI"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise RuntimeError(
        "MONGO_URI is missing. Add MONGO_URI or MONGODB_URI to .env."
    )


def baseline_schema() -> Dict[str, Any]:
    """Version 1 omits the transport fields the evaluator requires."""
    return {
        "name": "query_collection",
        "description": "Read a paginated collection from the operations API.",
        "method": "GET",
        "parameters": {
            "resource": "customers",
        },
    }


def baseline_rules() -> str:
    return (
        "You are the Hydra tool runtime.\n"
        "Execute the active tool schema exactly as stored.\n"
        "Do not invent parameters that the schema does not declare.\n"
        "When the schema declares them, every call must send api_version, "
        "pagination_limit, and auth_bearer."
    )


def _public(document: Dict[str, Any]) -> Dict[str, Any]:
    public = copy.deepcopy(document)
    if "_id" in public:
        public["_id"] = str(public["_id"])
    for key in ("created_at", "updated_at"):
        value = public.get(key)
        if isinstance(value, datetime):
            public[key] = value.isoformat()
    return public


def connect() -> tuple:
    """Open Atlas, ping it, and return ``(client, database)``."""
    global _client, _database
    if _client is not None and _database is not None:
        return _client, _database

    uri = mongo_uri()
    db_name = os.environ.get("MONGODB_DB", DATABASE_NAME).strip() or DATABASE_NAME
    client = MongoClient(
        uri,
        server_api=ServerApi("1"),
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=12000,
        retryWrites=True,
        w="majority",
        tz_aware=True,
        appname="HydraHarness",
    )
    client.admin.command("ping")
    database = client[db_name]
    _ensure_indexes(database[COLLECTION_NAME])
    _client = client
    _database = database
    return client, database


LATEST_INDEX = "agent_latest_version"


def _ensure_indexes(collection: Collection) -> None:
    """Compound index on (agent_id, version desc). The latest-version read is one index scan."""
    try:
        existing = collection.index_information()
    except PyMongoError:
        return
    for stale in ("agent_version", LATEST_INDEX):
        info = existing.get(stale)
        if info is None:
            continue
        if stale == LATEST_INDEX and "partialFilterExpression" not in info:
            continue
        try:
            collection.drop_index(stale)
        except PyMongoError:
            pass
    try:
        collection.create_index(
            [("agent_id", ASCENDING), ("version", DESCENDING)],
            unique=True,
            name=LATEST_INDEX,
        )
    except PyMongoError:
        return


def explain_latest(agent_id: str) -> Dict[str, Any]:
    """Explain the Task B read so the demo can show the index scan."""
    collection = _collection()
    plan = (
        collection.find({"agent_id": agent_id})
        .sort("version", DESCENDING)
        .limit(1)
        .explain()
    )
    stats = plan.get("executionStats", {})
    winning = plan.get("queryPlanner", {}).get("winningPlan", {})
    stage = winning
    index_name = None
    while isinstance(stage, dict):
        if stage.get("stage") == "IXSCAN":
            index_name = stage.get("indexName")
            break
        stage = stage.get("inputStage") or (stage.get("inputStages") or [None])[0]
    return {
        "index": index_name,
        "stage": "IXSCAN" if index_name else str(winning.get("stage")),
        "keys_examined": int(stats.get("totalKeysExamined", 0)),
        "docs_examined": int(stats.get("totalDocsExamined", 0)),
        "millis": int(stats.get("executionTimeMillis", 0)),
    }


def _collection() -> Collection:
    _, database = connect()
    return database[COLLECTION_NAME]


def get_latest_state(
    agent_id: str,
    initial_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return the newest state for an agent, seeding version 1 when absent."""
    collection = _collection()
    document = collection.find_one({"agent_id": agent_id}, sort=[("version", -1)])
    if document is not None:
        return _public(document)

    seeded = {
        "agent_id": agent_id,
        "session_id": agent_id,
        "version": 1,
        "tool_schema": copy.deepcopy(initial_schema) if initial_schema else baseline_schema(),
        "prompt_rules": baseline_rules(),
        "guardrails": [],
        "diagnosis": "baseline",
        "created_at": utcnow(),
    }
    try:
        collection.insert_one(seeded)
    except DuplicateKeyError:
        document = collection.find_one({"agent_id": agent_id}, sort=[("version", -1)])
        if document is None:
            raise
        return _public(document)
    return _public(seeded)


def save_mutated_state(
    agent_id: str,
    new_version: int,
    mutated_schema: Dict[str, Any],
    diagnosis: str,
    guardrails: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Insert a repaired agent version with an Atlas timestamp."""
    previous = get_latest_state(agent_id)
    rules = str(previous.get("prompt_rules") or baseline_rules()).rstrip()
    learned = [str(item).strip() for item in (guardrails or []) if str(item).strip()]
    learned_text = "\n".join("- {}".format(item) for item in learned) or "- no guardrail supplied"
    revision = (
        "{}\n\nContract revision v{}:\n{}\nDiagnosis: {}\n"
    ).format(rules, new_version, learned_text, diagnosis.strip())
    document = {
        "agent_id": agent_id,
        "session_id": agent_id,
        "version": int(new_version),
        "tool_schema": copy.deepcopy(mutated_schema),
        "prompt_rules": revision,
        "guardrails": learned,
        "diagnosis": diagnosis.strip(),
        "parent_version": previous.get("version"),
        "created_at": utcnow(),
    }
    result = _collection().insert_one(document)
    document["_id"] = result.inserted_id
    return _public(document)


def list_states(agent_id: str) -> List[Dict[str, Any]]:
    """Return every persisted version for the summary table."""
    cursor = _collection().find({"agent_id": agent_id}).sort("version", ASCENDING)
    return [_public(document) for document in cursor]


def close() -> None:
    global _client, _database
    if _client is not None:
        _client.close()
    _client = None
    _database = None
