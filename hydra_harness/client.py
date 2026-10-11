"""The installable interface other Python projects call."""

from __future__ import annotations

import os
from functools import wraps
from typing import Any, Callable, Dict, Optional

from . import database
from .harness import run_task


def schema_for_tool(tool_name: str, tool_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Seed a contract from the developer's tool. Transport fields stay absent until a repair adds them."""
    parameters = {
        key: value
        for key, value in tool_payload.items()
        if key != "endpoint"
    }
    return {
        "name": tool_name,
        "description": "Tool contract for {}.".format(tool_name),
        "method": "POST",
        "parameters": parameters,
    }


class HydraHarness:
    """Self-healing tool harness backed by MongoDB Atlas, Raindrop, and a process sandbox."""

    def __init__(
        self,
        mongo_uri: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        raindrop_key: Optional[str] = None,
        database_name: Optional[str] = None,
    ) -> None:
        self.mongo_uri = mongo_uri
        self.openai_api_key = openai_api_key
        self.raindrop_key = raindrop_key
        self.database_name = database_name

    def _configure(self) -> None:
        previous = os.environ.get("MONGO_URI", "")
        if self.mongo_uri:
            os.environ["MONGO_URI"] = self.mongo_uri
            if previous and previous != self.mongo_uri:
                database.close()
        if self.openai_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_api_key
        if self.raindrop_key:
            os.environ["RAINDROP_WRITE_KEY"] = self.raindrop_key
        if self.database_name:
            os.environ["MONGODB_DB"] = self.database_name

    def run_with_self_healing(
        self,
        agent_id: str,
        tool_name: str,
        tool_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run one tool call. On a schema failure, repair it in the sandbox and commit the next version."""
        if not isinstance(tool_payload, dict):
            raise TypeError("tool_payload must be a dict")
        self._configure()
        task = {
            "id": tool_name,
            "title": tool_name,
            "endpoint": str(tool_payload.get("endpoint") or "/{}".format(tool_name)),
            "payload": tool_payload,
        }
        report = run_task(
            agent_id,
            task,
            repair=True,
            initial_schema=schema_for_tool(tool_name, tool_payload),
        )
        version = report.version_saved if report.version_saved is not None else report.version_loaded
        return {
            "ok": report.ok,
            "message": report.message,
            "agent_id": agent_id,
            "tool_name": tool_name,
            "version": version,
            "repaired": report.repaired,
            "request": report.request,
            "diagnosis": report.diagnosis,
            "failure_message": report.failure_message,
            "sandbox_runs": report.sandbox_runs,
            "model_calls": report.model_calls,
            "prompt_tokens": report.prompt_tokens,
            "completion_tokens": report.completion_tokens,
        }

    def self_healing(self, agent_id: str, tool_name: str) -> Callable:
        """Decorator. The wrapped function runs only after the contract passes."""

        def decorate(fn: Callable) -> Callable:
            @wraps(fn)
            def wrapped(payload: Dict[str, Any], *args: Any, **kwargs: Any) -> Any:
                result = self.run_with_self_healing(agent_id, tool_name, payload)
                if not result["ok"]:
                    return result
                return fn(payload, *args, **kwargs)

            return wrapped

        return decorate
