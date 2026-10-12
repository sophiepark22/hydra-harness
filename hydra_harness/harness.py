"""Dual-loop engine: execute a tool, trace the failure, mutate, verify, persist."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import database
from . import evaluator
from . import memory
from . import sandbox

TASK_A: Dict[str, Any] = {
    "id": "A",
    "title": "List customers",
    "endpoint": "/v1/customers",
    "payload": {
        "resource": "customers",
        "endpoint": "/v1/customers",
    },
    "recall": False,
}

TASK_B: Dict[str, Any] = {
    "id": "B",
    "title": "List orders",
    "endpoint": "/v1/orders",
    "payload": {
        "resource": "orders",
        "endpoint": "/v1/orders",
        "idempotency_key": "ord-1042",
    },
}

GUARDRAIL_TEXT = "This agent may call query_collection only with an idempotency key."

TASK_RECALL: Dict[str, Any] = {
    "id": "R",
    "title": "List invoices",
    "endpoint": "/v1/invoices",
    "payload": {
        "resource": "invoices",
        "endpoint": "/v1/invoices",
    },
    "trace_query": (
        "A collection read was rejected. The invoices contract had no version header, "
        "no page size, and no bearer credential."
    ),
}

API_NOTE = (
    "Collection reads declare api_version, pagination_limit, and auth_bearer "
    "on the tool schema. pagination_limit is a positive integer no larger than 25. "
    "auth_bearer must be a secret reference, not a live credential. "
    "Store one guardrail: this agent may call the tool only with an idempotency key, "
    "and the schema must declare that key as idempotency_key. "
    "Return JSON with diagnosis, mutated_schema, and guardrails. "
    "Keep the current tool name and every existing field. Do not add a tool."
)

REPAIR_VALUES: Dict[str, Any] = {
    "api_version": "2024-10-01",
    "pagination_limit": 25,
    "auth_bearer": "${API_TOKEN}",
}

_PROPOSER_INSTRUCTIONS = (
    "You repair tool schemas for the Hydra recursive harness. "
    "Return a JSON object with diagnosis, mutated_schema, and guardrails. "
    "Use the failure and the API note. Choose the parameter values yourself. "
    "Preserve the tool name and every existing field. Do not add a tool."
)


@dataclass
class Proposal:
    schema: Dict[str, Any]
    diagnosis: str
    source: str
    note: Optional[str] = None
    guardrails: List[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    invoked_model: bool = False
    prompt: str = ""
    reply: str = ""


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
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    rejected_patches: List[Dict[str, Any]] = field(default_factory=list)
    guardrails: List[str] = field(default_factory=list)
    vector_match: Optional[Dict[str, Any]] = None


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
    parameters["idempotency_key"] = "${IDEMPOTENCY_KEY}"
    return Proposal(
        schema=mutated,
        diagnosis=(
            "Deterministic fallback, last resort: restored the transport fields "
            "and stored the idempotency guardrail."
        ),
        source="deterministic",
        note="deterministic fallback, last resort",
        guardrails=[GUARDRAIL_TEXT],
    )


def proposal_from_memory(schema: Dict[str, Any], hit: Dict[str, Any]) -> Proposal:
    """Apply a past repair onto the current tool without calling the model."""
    repaired = copy.deepcopy(schema)
    parameters = dict(repaired.get("parameters") or {})
    learned = hit.get("repair_parameters") or {}
    for name in ("api_version", "pagination_limit", "auth_bearer", "idempotency_key"):
        if learned.get(name) not in (None, ""):
            parameters[name] = learned[name]
    if "pagination_limit" in parameters:
        parameters["pagination_limit"] = _coerce_limit(parameters["pagination_limit"])
    repaired["parameters"] = parameters
    score = float(hit.get("score") or 0.0)
    return Proposal(
        schema=repaired,
        diagnosis="Reused a past repair found by Atlas Vector Search.",
        source="atlas-vector-search",
        note="matched {} at {:.2f}".format(hit.get("agent_id") or "a prior agent", score),
        guardrails=list(hit.get("guardrails") or []),
    )


def _guardrail_list(data: Dict[str, Any], mutated: Dict[str, Any]) -> List[str]:
    raw = data.get("guardrails")
    if raw is None:
        raw = mutated.get("guardrails")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _accepted_schema(current: Dict[str, Any], proposed: Dict[str, Any]) -> Dict[str, Any]:
    """Store a patch that already passed the invariant check."""
    accepted = copy.deepcopy(current)
    for key in ("description", "method"):
        if proposed.get(key) not in (None, ""):
            accepted[key] = proposed[key]
    accepted["name"] = current.get("name")
    parameters = dict(accepted.get("parameters") or {})
    parameters.update(evaluator._parameter_map(proposed))
    if "pagination_limit" in parameters:
        parameters["pagination_limit"] = _coerce_limit(parameters["pagination_limit"])
    accepted["parameters"] = parameters
    return accepted


def constraint_violation(
    current: Dict[str, Any],
    proposed: Dict[str, Any],
    guardrails: List[str],
) -> Optional[str]:
    """Reject a patch that drops a field, invents a tool, or skips the guardrail."""
    if not isinstance(proposed, dict):
        return "Rejected patch: schema was not an object."
    if isinstance(proposed.get("tools"), list):
        return "Rejected patch: invented an extra tool."
    if proposed.get("name") != current.get("name"):
        return "Rejected patch: invented a tool named {}.".format(proposed.get("name"))
    for key in ("description", "method"):
        if current.get(key) and proposed.get(key) in (None, ""):
            return "Rejected patch: dropped existing field {}.".format(key)
    current_params = evaluator._parameter_map(current)
    proposed_params = evaluator._parameter_map(proposed)
    for key in current_params:
        if key not in proposed_params:
            return "Rejected patch: dropped existing parameter {}.".format(key)
    missing = evaluator.missing_parameter(proposed)
    if missing:
        return "Rejected patch: still missing {}.".format(missing)
    limit = proposed_params.get("pagination_limit")
    if isinstance(limit, str) and str(limit).isdigit():
        limit = int(limit)
    if isinstance(limit, int) and not isinstance(limit, bool) and limit > 25:
        return "Rejected patch: pagination_limit must be 25 or less."
    if not proposed_params.get("idempotency_key"):
        return "Rejected patch: schema does not declare idempotency_key."
    text = " ".join(guardrails).lower()
    if "idempotency" not in text:
        return "Rejected patch: missing an idempotency guardrail."
    return None


def _parse_proposal(raw: str) -> Proposal:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("proposer returned a non-object")
    diagnosis = str(data.get("diagnosis") or "Outer loop proposed a schema repair.").strip()
    mutated = data.get("mutated_schema") or data.get("tool_schema") or data.get("schema")
    if not isinstance(mutated, dict):
        raise ValueError("proposer omitted mutated_schema")
    mutated = copy.deepcopy(mutated)
    guardrails = _guardrail_list(data, mutated)
    mutated.pop("guardrails", None)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    return Proposal(
        schema=mutated,
        diagnosis=diagnosis,
        source=model,
        guardrails=guardrails,
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
            proposal.note = "deterministic fallback, last resort (OPENAI_API_KEY is unset or still a placeholder)"
            return proposal
        try:
            return self._complete(key, model, schema, prompt_rules, failure, task, rejection)
        except Exception as exc:
            proposal = deterministic_mutation(schema, failure)
            proposal.note = "deterministic fallback, last resort (OpenAI request failed: {})".format(
                type(exc).__name__
            )
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
            "api_note": API_NOTE,
            "task": {"id": task["id"], "endpoint": task["endpoint"]},
            "prompt_rules": prompt_rules,
            "tool_schema": schema,
        }
        user_prompt = json.dumps(payload, indent=2)
        client = OpenAI(api_key=key, timeout=25.0)
        completion = client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _PROPOSER_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        )
        usage = completion.usage
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        content = completion.choices[0].message.content or ""
        try:
            proposal = _parse_proposal(content)
        except (ValueError, json.JSONDecodeError):
            proposal = deterministic_mutation(schema, failure)
            proposal.note = "deterministic fallback, last resort (model response was not a usable schema)"
        proposal.prompt_tokens = prompt_tokens
        proposal.completion_tokens = completion_tokens
        proposal.invoked_model = True
        proposal.prompt = "system:\n{}\n\nuser:\n{}".format(_PROPOSER_INSTRUCTIONS, user_prompt)
        proposal.reply = content
        return proposal


def inner_execution_loop(
    agent_id: str,
    task: Dict[str, Any],
    initial_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Load the active schema from Atlas and run one tool call."""
    state = database.get_latest_state(agent_id, initial_schema=initial_schema)
    schema = state["tool_schema"]
    guardrails = list(state.get("guardrails") or [])
    trial = sandbox.execute(
        schema,
        task["payload"],
        phase="execute",
        guardrails=guardrails,
    )
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


def _record_usage(usage: Dict[str, int], proposal: Proposal) -> None:
    if not proposal.invoked_model:
        return
    usage["model_calls"] += 1
    usage["prompt_tokens"] += proposal.prompt_tokens
    usage["completion_tokens"] += proposal.completion_tokens


def _rejected_patch(proposal: Proposal, reason: str) -> Dict[str, Any]:
    return {
        "reason": reason,
        "schema": copy.deepcopy(proposal.schema),
        "guardrails": list(proposal.guardrails),
        "source": proposal.source,
    }


def _prove(proposal: Proposal, task: Dict[str, Any]) -> tuple:
    bare = {
        key: value
        for key, value in task["payload"].items()
        if key != "idempotency_key"
    }
    compliant = dict(task["payload"])
    key = compliant.get("idempotency_key")
    if not (isinstance(key, str) and key.strip()):
        compliant["idempotency_key"] = "sandbox-verify-1"
    blocked = sandbox.execute(
        proposal.schema,
        bare,
        phase="guardrail",
        guardrails=proposal.guardrails,
    )
    verified = sandbox.execute(
        proposal.schema,
        compliant,
        phase="verify",
        guardrails=proposal.guardrails,
    )
    return blocked, verified


def outer_mutation_loop(
    agent_id: str,
    state: Dict[str, Any],
    failure: str,
    task: Dict[str, Any],
    on_stage: Optional[Any] = None,
) -> Dict[str, Any]:
    """Propose a repair, reject a bad patch, prove the rest in the sandbox, then commit."""

    def stage(name: str, **info: Any) -> None:
        if on_stage is not None:
            on_stage(name, info)

    proposer = LLMProposer()
    schema = state["tool_schema"]
    rules = str(state.get("prompt_rules") or "")
    trials = []
    rejected: List[Dict[str, Any]] = []
    usage = {"model_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    rejection: Optional[str] = None
    chosen: Optional[Proposal] = None
    vector_match: Optional[Dict[str, Any]] = None
    query = str(task.get("trace_query") or "").strip() or memory.trace_text(task, failure, schema)
    wait = 25.0 if task.get("trace_query") else 0.0
    hit = memory.recall(query, timeout_seconds=wait) if task.get("recall", True) else None
    if hit is not None:
        candidate = proposal_from_memory(schema, hit)
        violation = constraint_violation(schema, candidate.schema, candidate.guardrails)
        if violation:
            rejected.append(_rejected_patch(candidate, violation))
        else:
            chosen = candidate
            vector_match = {
                "agent_id": hit.get("agent_id"),
                "score": hit.get("score"),
                "error_text": hit.get("error_text"),
                "trace_text": hit.get("trace_text"),
                "endpoint": hit.get("endpoint"),
            }

    if chosen is None:
        stage("model")
        for _ in range(2):
            proposal = proposer.propose(schema, rules, failure, task, rejection=rejection)
            _record_usage(usage, proposal)
            if proposal.source == "deterministic":
                chosen = proposal
                break
            violation = constraint_violation(schema, proposal.schema, proposal.guardrails)
            if violation:
                rejected.append(_rejected_patch(proposal, violation))
                rejection = violation
                continue
            chosen = proposal
            chosen.schema = _accepted_schema(schema, proposal.schema)
            break

    if chosen is None:
        chosen = deterministic_mutation(schema, failure)

    stage(
        "model",
        done=True,
        proposer=chosen.source,
        model_calls=usage["model_calls"],
        tokens=usage["prompt_tokens"] + usage["completion_tokens"],
        diagnosis=chosen.diagnosis,
        prompt=chosen.prompt,
        reply=chosen.reply,
    )
    stage("prove")
    blocked, verified = _prove(chosen, task)
    trials.extend((blocked, verified))
    if (not verified.ok or blocked.ok) and chosen.source != "deterministic":
        reason = (
            verified.message
            if not verified.ok
            else "Sandbox rejected the patch: a call without idempotency_key was allowed."
        )
        rejected.append(_rejected_patch(chosen, reason))
        chosen = deterministic_mutation(schema, failure)
        blocked, verified = _prove(chosen, task)
        trials.extend((blocked, verified))
    if not verified.ok or blocked.ok:
        raise RuntimeError(verified.message if not verified.ok else blocked.message)

    verified.gate = True
    stage("prove", sandbox=verified.as_dict())
    saved = database.save_mutated_state(
        agent_id,
        int(state["version"]) + 1,
        chosen.schema,
        chosen.diagnosis,
        guardrails=chosen.guardrails,
        model_prompt=chosen.prompt,
        model_reply=chosen.reply,
        model_name=chosen.source,
    )
    stage(
        "commit",
        version=int(saved["version"]),
        document_id=str(saved.get("_id") or ""),
        guardrails=list(saved.get("guardrails") or []),
        schema_before=schema,
        schema_after=saved["tool_schema"],
    )
    stage("embed")
    memory.remember(
        agent_id,
        task,
        failure,
        schema,
        evaluator._parameter_map(chosen.schema),
        list(chosen.guardrails),
        chosen.diagnosis,
    )
    return {
        "proposal": chosen,
        "verify_message": verified.message,
        "saved": saved,
        "request": verified.request,
        "sandbox_runs": [item.as_dict() for item in trials],
        "model_calls": usage["model_calls"],
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "rejected_patches": rejected,
        "vector_match": vector_match,
    }


def run_task(
    agent_id: str,
    task: Dict[str, Any],
    *,
    repair: bool,
    initial_schema: Optional[Dict[str, Any]] = None,
    on_stage: Optional[Any] = None,
) -> HarnessReport:
    """Inner loop first. The outer loop runs only after a failed execution."""

    def stage(name: str, **info: Any) -> None:
        if on_stage is not None:
            on_stage(name, info)

    if repair:
        stage("fail", message="Running the tool in the sandbox.", sandbox={})
    first = inner_execution_loop(agent_id, task, initial_schema=initial_schema)
    state = first["state"]
    spans = [first["span"]] if first["span"] else []
    sandbox_runs = [first["sandbox"]] if first.get("sandbox") else []

    guardrail_block = str(first["message"]).startswith("Guardrail")
    if first["ok"] or not repair or guardrail_block:
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

    stage("fail", message=first["message"], sandbox=first.get("sandbox") or {})
    stage("trace", message=first["message"])
    mutation = outer_mutation_loop(agent_id, state, first["message"], task, on_stage=on_stage)
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
        model_calls=int(mutation["model_calls"]),
        prompt_tokens=int(mutation["prompt_tokens"]),
        completion_tokens=int(mutation["completion_tokens"]),
        rejected_patches=list(mutation["rejected_patches"]),
        guardrails=list(saved.get("guardrails") or []),
        vector_match=mutation.get("vector_match"),
    )
