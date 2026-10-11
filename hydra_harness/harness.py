"""Dual-loop engine: execute a tool, trace the failure, mutate, verify, persist."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import database
from . import sandbox

TASK_A: Dict[str, Any] = {
    "id": "A",
    "title": "List customers",
    "endpoint": "/v1/customers",
    "payload": {
        "resource": "customers",
        "endpoint": "/v1/customers",
    },
}

TASK_B: Dict[str, Any] = {
    "id": "B",
    "title": "List orders",
    "endpoint": "/v1/orders",
    "payload": {
        "resource": "orders",
        "endpoint": "/v1/orders",
    },
}

REPAIR_VALUES: Dict[str, Any] = {
    "api_version": "2024-10-01",
    "pagination_limit": 25,
    "auth_bearer": "${API_TOKEN}",
}

_PROPOSER_INSTRUCTIONS = (
    "You repair tool schemas for the Hydra recursive harness. "
    "Return a JSON object with two keys: diagnosis (one sentence) and "
    "mutated_schema (the full tool schema). "
    "Preserve name, description, method, and every existing parameter. "
    "Add any missing required parameters inside parameters using exactly "
    "these values: api_version='2024-10-01', pagination_limit=25, "
    "auth_bearer='${API_TOKEN}'. Do not remove fields and do not add tools."
)


@dataclass
class Proposal:
    schema: Dict[str, Any]
    diagnosis: str
    source: str
    note: Optional[str] = None


@dataclass
class HarnessReport:
    task_id: str
    title: str
    endpoint: str
    ok: bool
    message: str
    failure_message: Optional[str]
    attempts: int
    version_loaded: int
    version_saved: Optional[int]
    schema_before: Dict[str, Any]
    schema_after: Optional[Dict[str, Any]]
    diagnosis: Optional[str]
    proposer: Optional[str]
    proposer_note: Optional[str]
    repaired: bool
    document_id: Optional[str]
    request: Optional[Dict[str, Any]]
    spans: List[Dict[str, Any]] = field(default_factory=list)
    sandbox_runs: List[Dict[str, Any]] = field(default_factory=list)


class TelemetryIntercept:
    """Record execution failures in Raindrop, or as structured local spans."""

    def __init__(self) -> None:
        self.backend = "structured-fallback"
        self.spans: List[Dict[str, Any]] = []
        self._raindrop: Any = None
        key, source = _raindrop_credential()
        if source == "missing":
            self.note = "RAINDROP_WRITE_KEY is unset; using structured traces"
            return
        if not key:
            self.note = "Raindrop key is still a placeholder; using structured traces"
            return
        try:
            import warnings

            warnings.filterwarnings(
                "ignore",
                message="urllib3 v2 only supports OpenSSL",
            )
            import raindrop.analytics as raindrop

            if hasattr(raindrop, "init"):
                try:
                    raindrop.init(
                        key,
                        tracing_enabled=True,
                        auto_instrument=False,
                        bypass_otel_for_tools=True,
                    )
                except TypeError:
                    raindrop.init(key)
            elif hasattr(raindrop, "write_key"):
                raindrop.write_key = key
            else:
                raise RuntimeError("raindrop SDK has no init hook")
            self._raindrop = raindrop
            self.backend = "raindrop-cloud"
            self.note = "spans exported to Raindrop Cloud via {}".format(source)
            try:
                import logging

                logging.getLogger("raindrop.analytics").setLevel(logging.ERROR)
            except Exception:
                pass
        except Exception as exc:
            self.note = "raindrop-ai unavailable ({}); using structured traces".format(
                type(exc).__name__
            )

    def intercept(
        self,
        *,
        agent_id: str,
        task: Dict[str, Any],
        version: int,
        schema: Dict[str, Any],
        error: str,
    ) -> Dict[str, Any]:
        span = {
            "span": "tool.execution",
            "agent_id": agent_id,
            "task": task["id"],
            "endpoint": task["endpoint"],
            "version": version,
            "status": "error",
            "error": error,
            "backend": self.backend,
            "parameters": sorted((schema.get("parameters") or {}).keys()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.spans.append(span)
        if self._raindrop is not None:
            self._export(span, schema, error)
        return span

    def _export(self, span: Dict[str, Any], schema: Dict[str, Any], error: str) -> None:
        try:
            if hasattr(self._raindrop, "begin"):
                self._export_interaction(span, schema, error)
            elif hasattr(self._raindrop, "track_ai"):
                event_id = self._raindrop.track_ai(
                    user_id=span["agent_id"],
                    event="hydra_tool_failure",
                    model="hydra-harness",
                    user_input=json.dumps(
                        {
                            "tool": schema.get("name", "query_collection"),
                            "endpoint": span["endpoint"],
                            "schema_version": span["version"],
                            "parameters": span["parameters"],
                        }
                    ),
                    output=error,
                    convo_id=span["agent_id"],
                    properties={
                        "task": str(span["task"]),
                        "version": int(span["version"]),
                    },
                )
                if event_id:
                    span["raindrop_event_id"] = str(event_id)
        except Exception as exc:
            span["raindrop_error"] = type(exc).__name__

    def _export_interaction(
        self,
        span: Dict[str, Any],
        schema: Dict[str, Any],
        error: str,
    ) -> None:
        interaction = self._raindrop.begin(
            user_id=span["agent_id"],
            event="hydra_tool_failure",
            input=json.dumps(
                {"task": span["task"], "endpoint": span["endpoint"], "error": error}
            ),
            convo_id=span["agent_id"],
            properties={"version": str(span["version"])},
        )
        if hasattr(interaction, "track_tool"):
            interaction.track_tool(
                name=schema.get("name", "query_collection"),
                input={
                    "endpoint": span["endpoint"],
                    "schema_version": span["version"],
                    "parameters": span["parameters"],
                },
                error=error,
                properties={"task": span["task"]},
            )
        interaction.finish(output=error)
        event_id = getattr(interaction, "id", None)
        if event_id:
            span["raindrop_event_id"] = str(event_id)

    def record(
        self,
        *,
        agent_id: str,
        event: str,
        task: Dict[str, Any],
        version: int,
        output: str,
    ) -> None:
        """Send a successful harness step to Raindrop Cloud when a key is configured."""
        if self._raindrop is None:
            return
        payload = json.dumps(
            {
                "task": task["id"],
                "endpoint": task["endpoint"],
                "version": version,
                "output": output,
            }
        )
        try:
            if hasattr(self._raindrop, "begin"):
                interaction = self._raindrop.begin(
                    user_id=agent_id,
                    event=event,
                    input=payload,
                    convo_id=agent_id,
                    properties={"version": str(version), "task": str(task["id"])},
                )
                interaction.finish(output=output)
            elif hasattr(self._raindrop, "track_ai"):
                self._raindrop.track_ai(
                    user_id=agent_id,
                    event=event,
                    model="hydra-harness",
                    user_input=payload,
                    output=output,
                    convo_id=agent_id,
                    properties={"task": str(task["id"]), "version": int(version)},
                )
        except Exception:
            return

    def shutdown(self) -> None:
        if self._raindrop is None:
            return
        try:
            self._raindrop.flush()
        except Exception:
            pass
        try:
            self._raindrop.shutdown()
        except Exception:
            pass


_telemetry: Optional[TelemetryIntercept] = None


def telemetry() -> TelemetryIntercept:
    global _telemetry
    if _telemetry is None:
        _telemetry = TelemetryIntercept()
    return _telemetry


def _raindrop_credential() -> tuple:
    """Prefer the write key from ``raindrop cloud setup``, then RAINDROP_API_KEY."""
    write_key = os.getenv("RAINDROP_WRITE_KEY", "").strip()
    api_key = os.getenv("RAINDROP_API_KEY", "").strip()
    if _configured_secret(write_key):
        return write_key, "RAINDROP_WRITE_KEY"
    if _configured_secret(api_key):
        return api_key, "RAINDROP_API_KEY"
    if write_key or api_key:
        return "", "placeholder"
    return "", "missing"


def _configured_secret(value: str) -> bool:
    text = (value or "").strip()
    if not text or text.endswith("...") or "..." in text:
        return False
    return True


def _coerce_limit(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value


def deterministic_mutation(schema: Dict[str, Any], failure: str) -> Proposal:
    mutated = copy.deepcopy(schema)
    parameters = dict(mutated.get("parameters") or {})
    parameters.update(REPAIR_VALUES)
    parameters["pagination_limit"] = _coerce_limit(parameters["pagination_limit"])
    mutated["parameters"] = parameters
    return Proposal(
        schema=mutated,
        diagnosis=(
            "Added missing transport parameters api_version, pagination_limit, and auth_bearer."
        ),
        source="deterministic",
    )


def _merge_schema(current: Dict[str, Any], proposed: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(current)
    for key, value in proposed.items():
        if key == "parameters" and isinstance(value, dict):
            continue
        merged[key] = copy.deepcopy(value)

    parameters = dict(current.get("parameters") or {})
    incoming = proposed.get("parameters")
    if isinstance(incoming, dict):
        properties = incoming.get("properties")
        if isinstance(properties, dict):
            for name, spec in properties.items():
                if isinstance(spec, dict):
                    if "const" in spec:
                        parameters[name] = spec["const"]
                    elif "default" in spec:
                        parameters[name] = spec["default"]
                    elif "example" in spec:
                        parameters[name] = spec["example"]
                elif spec not in (None, ""):
                    parameters[name] = spec
        for name, spec in incoming.items():
            if name in ("type", "required", "properties", "additionalProperties"):
                continue
            if spec not in (None, ""):
                parameters[name] = spec
    if "pagination_limit" in parameters:
        parameters["pagination_limit"] = _coerce_limit(parameters["pagination_limit"])
    merged["parameters"] = parameters
    return merged


def _parse_proposal(raw: str, current: Dict[str, Any]) -> Proposal:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("proposer returned a non-object")
    diagnosis = str(data.get("diagnosis") or "Outer loop proposed a schema repair.").strip()
    mutated = data.get("mutated_schema") or data.get("tool_schema") or data.get("schema")
    if not isinstance(mutated, dict):
        raise ValueError("proposer omitted mutated_schema")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    return Proposal(
        schema=_merge_schema(current, mutated),
        diagnosis=diagnosis,
        source=model,
    )


class LLMProposer:
    """Ask gpt-4o-mini for a targeted schema repair. Fall back if the key is absent."""

    def propose(
        self,
        schema: Dict[str, Any],
        prompt_rules: str,
        failure: str,
        task: Dict[str, Any],
        rejection: Optional[str] = None,
    ) -> Proposal:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        if not _configured_secret(key):
            proposal = deterministic_mutation(schema, failure)
            proposal.note = "OPENAI_API_KEY is unset or still a placeholder"
            return proposal
        try:
            return self._complete(key, model, schema, prompt_rules, failure, task, rejection)
        except Exception as exc:
            proposal = deterministic_mutation(schema, failure)
            proposal.note = "OpenAI request failed ({})".format(type(exc).__name__)
            return proposal

    def _complete(
        self,
        key: str,
        model: str,
        schema: Dict[str, Any],
        prompt_rules: str,
        failure: str,
        task: Dict[str, Any],
        rejection: Optional[str],
    ) -> Proposal:
        from openai import OpenAI

        payload = {
            "failure": failure,
            "previous_rejection": rejection,
            "task": {"id": task["id"], "endpoint": task["endpoint"]},
            "prompt_rules": prompt_rules,
            "tool_schema": schema,
            "required_parameter_values": REPAIR_VALUES,
        }
        client = OpenAI(api_key=key, timeout=25.0)
        completion = client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _PROPOSER_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": json.dumps(payload),
                },
            ],
        )
        content = completion.choices[0].message.content or ""
        return _parse_proposal(content, schema)


def inner_execution_loop(
    agent_id: str,
    task: Dict[str, Any],
    initial_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Load the active schema from Atlas and run one tool call."""
    state = database.get_latest_state(agent_id, initial_schema=initial_schema)
    schema = state["tool_schema"]
    trial = sandbox.execute(schema, task["payload"], phase="execute")
    ok, message = trial.ok, trial.message
    span = None
    if not ok:
        span = telemetry().intercept(
            agent_id=agent_id,
            task=task,
            version=int(state["version"]),
            schema=schema,
            error=message,
        )
    return {
        "state": state,
        "ok": ok,
        "message": message,
        "span": span,
        "request": trial.request,
        "sandbox": trial.as_dict(),
    }


def outer_mutation_loop(
    agent_id: str,
    state: Dict[str, Any],
    failure: str,
    task: Dict[str, Any],
) -> Dict[str, Any]:
    """Propose a repair, prove it in the sandbox, then write the next Atlas version."""
    proposer = LLMProposer()
    schema = state["tool_schema"]
    rules = str(state.get("prompt_rules") or "")
    trials = []
    proposal = proposer.propose(schema, rules, failure, task)
    trial = sandbox.execute(proposal.schema, task["payload"], phase="verify")
    trials.append(trial)
    if not trial.ok and proposal.source != "deterministic":
        proposal = proposer.propose(
            schema,
            rules,
            failure,
            task,
            rejection=trial.message,
        )
        trial = sandbox.execute(proposal.schema, task["payload"], phase="verify")
        trials.append(trial)
    if not trial.ok:
        proposal = deterministic_mutation(schema, failure)
        trial = sandbox.execute(proposal.schema, task["payload"], phase="verify")
        trials.append(trial)
    if not trial.ok:
        raise RuntimeError(trial.message)

    trial.gate = True
    saved = database.save_mutated_state(
        agent_id,
        int(state["version"]) + 1,
        proposal.schema,
        proposal.diagnosis,
    )
    return {
        "proposal": proposal,
        "verify_message": trial.message,
        "saved": saved,
        "request": trial.request,
        "sandbox_runs": [item.as_dict() for item in trials],
    }


def run_task(
    agent_id: str,
    task: Dict[str, Any],
    *,
    repair: bool,
    initial_schema: Optional[Dict[str, Any]] = None,
) -> HarnessReport:
    """Inner loop first. The outer loop runs only after a failed execution."""
    first = inner_execution_loop(agent_id, task, initial_schema=initial_schema)
    state = first["state"]
    spans = [first["span"]] if first["span"] else []
    sandbox_runs = [first["sandbox"]] if first.get("sandbox") else []

    if first["ok"] or not repair:
        if first["ok"]:
            telemetry().record(
                agent_id=agent_id,
                event="hydra_task_success",
                task=task,
                version=int(state["version"]),
                output=first["message"],
            )
        return HarnessReport(
            task_id=task["id"],
            title=task["title"],
            endpoint=task["endpoint"],
            ok=bool(first["ok"]),
            message=first["message"],
            failure_message=None if first["ok"] else first["message"],
            attempts=1,
            version_loaded=int(state["version"]),
            version_saved=None,
            schema_before=state["tool_schema"],
            schema_after=None,
            diagnosis=None,
            proposer=None,
            proposer_note=None,
            repaired=False,
            document_id=str(state.get("_id")) if state.get("_id") else None,
            request=first["request"],
            spans=spans,
            sandbox_runs=sandbox_runs,
        )

    mutation = outer_mutation_loop(agent_id, state, first["message"], task)
    saved = mutation["saved"]
    proposal: Proposal = mutation["proposal"]
    sandbox_runs.extend(mutation["sandbox_runs"])
    telemetry().record(
        agent_id=agent_id,
        event="hydra_schema_repair",
        task=task,
        version=int(saved["version"]),
        output=proposal.diagnosis,
    )
    return HarnessReport(
        task_id=task["id"],
        title=task["title"],
        endpoint=task["endpoint"],
        ok=True,
        message=mutation["verify_message"],
        failure_message=first["message"],
        attempts=1,
        version_loaded=int(state["version"]),
        version_saved=int(saved["version"]),
        schema_before=state["tool_schema"],
        schema_after=saved["tool_schema"],
        diagnosis=proposal.diagnosis,
        proposer=proposal.source,
        proposer_note=proposal.note,
        repaired=True,
        document_id=str(saved.get("_id")) if saved.get("_id") else None,
        request=mutation["request"],
        spans=spans,
        sandbox_runs=sandbox_runs,
    )
