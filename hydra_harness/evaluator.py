"""Deterministic checks for a proposed tool schema."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# Transport fields every repaired contract must declare.
REQUIRED_PARAMETERS = ("api_version", "pagination_limit", "auth_bearer")

SUCCESS_MESSAGE = "Execution verified successfully!"


def _parameter_map(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Collect concrete parameter values from the schema body."""
    found: Dict[str, Any] = {}
    for name in REQUIRED_PARAMETERS:
        if name in schema and schema[name] not in (None, ""):
            found[name] = schema[name]

    parameters = schema.get("parameters")
    if not isinstance(parameters, dict):
        return found

    properties = parameters.get("properties")
    if isinstance(properties, dict):
        for name, spec in properties.items():
            if isinstance(spec, dict):
                if "const" in spec:
                    found[name] = spec["const"]
                elif "default" in spec:
                    found[name] = spec["default"]
                elif "example" in spec:
                    found[name] = spec["example"]
            elif spec not in (None, ""):
                found[name] = spec

    for name, value in parameters.items():
        if name in ("type", "required", "properties", "additionalProperties"):
            continue
        if value not in (None, ""):
            found[name] = value
    return found


def _usable(name: str, value: Any) -> bool:
    if value is None or value == "":
        return False
    if name == "api_version":
        return isinstance(value, str)
    if name == "auth_bearer":
        return isinstance(value, str)
    if name == "pagination_limit":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return value > 0
        if isinstance(value, str) and value.isdigit():
            return int(value) > 0
        return False
    return True


def missing_parameter(schema: Dict[str, Any]) -> Optional[str]:
    present = _parameter_map(schema if isinstance(schema, dict) else {})
    for name in REQUIRED_PARAMETERS:
        if not _usable(name, present.get(name)):
            return name
    return None


def execution_request(schema: Dict[str, Any], test_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Bind a verified schema and payload into the request the tool would send."""
    present = _parameter_map(schema)
    limit = present.get("pagination_limit")
    if isinstance(limit, str) and limit.isdigit():
        limit = int(limit)
    headers = {
        "Authorization": "Bearer {}".format(present.get("auth_bearer")),
        "X-Api-Version": present.get("api_version"),
    }
    idempotency_key = test_payload.get("idempotency_key")
    if isinstance(idempotency_key, str) and idempotency_key.strip():
        headers["Idempotency-Key"] = idempotency_key.strip()
    return {
        "tool": schema.get("name", "query_collection"),
        "method": schema.get("method", "GET"),
        "endpoint": test_payload.get("endpoint"),
        "query": {
            "resource": test_payload.get("resource"),
            "limit": limit,
        },
        "headers": headers,
    }


def guardrail_failure(
    test_payload: Dict[str, Any],
    guardrails: Optional[list] = None,
) -> Optional[str]:
    """Reject a call that breaks a guardrail stored on the active version."""
    rules = [str(item).lower() for item in (guardrails or [])]
    if not any("idempotency" in rule for rule in rules):
        return None
    key = test_payload.get("idempotency_key") if isinstance(test_payload, dict) else None
    if isinstance(key, str) and key.strip():
        return None
    return "Guardrail rejected the call: idempotency_key is required."


def validate_tool_execution(
    schema: Dict[str, Any],
    test_payload: Dict[str, Any],
    guardrails: Optional[list] = None,
) -> Tuple[bool, str]:
    """Run a test execution against the schema.

    Missing transport fields fail closed. A stored idempotency guardrail
    rejects a payload that does not carry idempotency_key.
    """
    if not isinstance(schema, dict) or not isinstance(test_payload, dict):
        return False, "Missing required tool parameter: api_version"

    missing = missing_parameter(schema)
    if missing:
        return False, "Missing required tool parameter: {}".format(missing)

    present = _parameter_map(schema)
    limit = present.get("pagination_limit")
    if isinstance(limit, str) and limit.isdigit():
        limit = int(limit)
    if isinstance(limit, int) and limit > 25:
        return False, "Guardrail rejected the call: pagination_limit must be 25 or less."

    blocked = guardrail_failure(test_payload, guardrails)
    if blocked:
        return False, blocked

    request = execution_request(schema, test_payload)
    if not request.get("endpoint") and not request["query"].get("resource"):
        return False, "Missing required tool parameter: api_version"
    return True, SUCCESS_MESSAGE


if __name__ == "__main__":
    broken = {"name": "query_collection", "parameters": {"resource": "customers"}}
    payload = {"resource": "customers", "endpoint": "/v1/customers"}
    assert validate_tool_execution(broken, payload) == (
        False,
        "Missing required tool parameter: api_version",
    )
    repaired = {
        "name": "query_collection",
        "method": "GET",
        "parameters": {
            "resource": "customers",
            "api_version": "2024-10-01",
            "pagination_limit": 25,
            "auth_bearer": "${API_TOKEN}",
        },
    }
    assert validate_tool_execution(repaired, payload) == (True, SUCCESS_MESSAGE)
    rule = ["This agent may call query_collection only with an idempotency key."]
    assert validate_tool_execution(repaired, payload, rule) == (
        False,
        "Guardrail rejected the call: idempotency_key is required.",
    )
    covered = dict(payload)
    covered["idempotency_key"] = "cust-1"
    assert validate_tool_execution(repaired, covered, rule)[0] is True
    print(SUCCESS_MESSAGE)
