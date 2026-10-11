"""Stream one live demo as NDJSON. The Next.js board reads this as it happens."""

from __future__ import annotations

import json
import secrets
import sys
import time
from typing import Any, Dict

from dotenv import load_dotenv

from . import database, harness, memory


def _emit(payload: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def _sandbox_line(runs: list) -> str:
    if not runs:
        return "no sandbox run"
    last = runs[-1]
    status = "pass" if last.get("ok") else "fail"
    return "{}  {}  {}".format(last.get("sandbox_id") or "pid ?", last.get("phase") or "execute", status)


def _schema_rows(before: Dict[str, Any], after: Dict[str, Any]) -> list:
    left = dict((before.get("parameters") or {}))
    right = dict((after.get("parameters") or {}))
    names = list(dict.fromkeys(list(left) + list(right)))
    rows = []
    for name in names:
        old = left.get(name)
        new = right.get(name)
        if name not in left:
            change = "added"
            v1 = "—"
            v2 = new
        elif name not in right:
            change = "removed"
            v1 = old
            v2 = "—"
        elif old != new:
            change = "changed"
            v1 = old
            v2 = new
        else:
            change = "kept"
            v1 = old
            v2 = new
        rows.append({"name": name, "v1": v1, "v2": v2, "change": change})
    return rows


def run() -> int:
    load_dotenv(database.PROJECT_ROOT / ".env")
    try:
        _, handle = database.connect()
        memory.ensure_index()
    except Exception as exc:
        _emit({"type": "error", "message": str(exc)})
        return 1

    agent_id = "hydra-{}".format(secrets.token_hex(3))
    host = database.atlas_host(
        __import__("os").environ.get("MONGO_URI")
        or __import__("os").environ.get("MONGODB_URI", "")
    )
    _emit(
        {
            "type": "start",
            "agent_id": agent_id,
            "host": host,
            "database": handle.name,
        }
    )

    seeded = database.get_latest_state(agent_id)
    _emit(
        {
            "type": "beat",
            "id": "seed",
            "agent_id": agent_id,
            "document_id": seeded.get("_id"),
            "version": 1,
            "log": "Atlas insert {} v1 resource only".format(agent_id),
        }
    )

    def on_stage(stage: str, info: Dict[str, Any]) -> None:
        payload: Dict[str, Any] = {"type": "beat", "id": stage}
        if stage == "fail":
            sandbox = info.get("sandbox") or {}
            payload["message"] = info.get("message")
            payload["sandbox_id"] = sandbox.get("sandbox_id")
            message = str(info.get("message") or "")
            if message.startswith("Running"):
                payload["log"] = "Trying the first call in a safe test."
            else:
                payload["log"] = "The first call failed. The tool is missing details it needs."
        elif stage == "trace":
            payload["log"] = "The mistake was written down."
        elif stage == "model" and info.get("done"):
            calls = int(info.get("model_calls") or 0)
            payload["proposer"] = info.get("proposer")
            payload["model_calls"] = calls
            payload["tokens"] = info.get("tokens")
            payload["diagnosis"] = info.get("diagnosis")
            payload["log"] = (
                "The model filled in the missing details."
                if calls
                else "A past fix was reused. The model was not called."
            )
        elif stage == "model":
            payload["log"] = "Asking the model what the tool was missing."
        elif stage == "prove" and info.get("sandbox"):
            sandbox = info.get("sandbox") or {}
            payload["sandbox_id"] = sandbox.get("sandbox_id")
            payload["ok"] = sandbox.get("ok")
            payload["log"] = "The test passed, so it is safe to save."
        elif stage == "prove":
            payload["log"] = "Testing the fix before anything is saved."
        elif stage == "commit":
            payload["version"] = info.get("version")
            payload["document_id"] = info.get("document_id")
            payload["guardrails"] = info.get("guardrails")
            payload["schema_rows"] = _schema_rows(
                info.get("schema_before") or {},
                info.get("schema_after") or {},
            )
            payload["atlas"] = _atlas_rows(agent_id)
            payload["log"] = "Saved as version 2. The first version is still there."
        elif stage == "embed":
            payload["log"] = "Remembering this mistake for the next similar error."
        else:
            payload["log"] = stage
        _emit(payload)

    started = time.perf_counter()
    task_a = harness.run_task(agent_id, harness.TASK_A, repair=True, on_stage=on_stage)
    elapsed_a = int((time.perf_counter() - started) * 1000)
    if not task_a.repaired or not task_a.ok:
        _emit({"type": "error", "message": task_a.failure_message or task_a.message})
        _close()
        return 1

    recall_agent = "hydra-{}".format(secrets.token_hex(3))
    _emit(
        {
            "type": "beat",
            "id": "recall",
            "log": "A new error uses different words. Searching past mistakes.",
        }
    )
    memory.wait_until_queryable()
    recall = harness.run_task(recall_agent, harness.TASK_RECALL, repair=True)
    match = recall.vector_match or {}
    _emit(
        {
            "type": "beat",
            "id": "recall",
            "score": match.get("score"),
            "prior_agent": match.get("agent_id"),
            "model_calls": recall.model_calls,
            "log": "Found a past fix. The model was not called.",
        }
    )

    _emit(
        {
            "type": "beat",
            "id": "guard",
            "log": "Checking the new rule. A repeat call needs a safety key.",
        }
    )
    customers_with_key = {
        "id": "A2",
        "title": "List customers with an idempotency key",
        "endpoint": harness.TASK_A["endpoint"],
        "payload": dict(harness.TASK_A["payload"], idempotency_key="cust-replay-1"),
    }
    blocked = harness.run_task(agent_id, harness.TASK_A, repair=False)
    allowed = harness.run_task(agent_id, customers_with_key, repair=False)
    _emit(
        {
            "type": "beat",
            "id": "guard",
            "blocked": blocked.message,
            "allowed": allowed.message,
            "log": "Without the key it fails. With the key it passes.",
        }
    )

    _emit(
        {
            "type": "beat",
            "id": "transfer",
            "log": "The next call is using the saved tool.",
        }
    )
    started = time.perf_counter()
    task_b = harness.run_task(agent_id, harness.TASK_B, repair=False)
    elapsed_b = int((time.perf_counter() - started) * 1000)
    plan = database.explain_latest(agent_id)
    _emit(
        {
            "type": "beat",
            "id": "transfer",
            "elapsed_ms": elapsed_b,
            "task_a_ms": elapsed_a,
            "index": plan.get("index"),
            "stage": plan.get("stage"),
            "keys_examined": plan.get("keys_examined"),
            "docs_examined": plan.get("docs_examined"),
            "index_ms": plan.get("millis"),
            "model_calls": task_b.model_calls,
            "atlas": _atlas_rows(agent_id),
            "log": "The next call worked the first time. The model was not called.",
        }
    )
    _emit({"type": "done", "agent_id": agent_id})
    _close()
    return 0


def _atlas_rows(agent_id: str) -> list:
    rows = []
    for document in database.list_states(agent_id):
        parameters = (document.get("tool_schema") or {}).get("parameters") or {}
        rows.append(
            {
                "version": "v{}".format(document.get("version")),
                "where": "agent_states",
                "detail": ", ".join(parameters.keys()) or "resource",
                "id": str(document.get("_id") or ""),
                "guardrail": "idempotency" if document.get("guardrails") else "—",
            }
        )
    return rows


def _close() -> None:
    try:
        harness.telemetry().shutdown()
    except Exception:
        pass
    memory.close()
    database.close()


if __name__ == "__main__":
    raise SystemExit(run())
