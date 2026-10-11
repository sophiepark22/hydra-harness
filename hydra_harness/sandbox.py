"""Ephemeral process sandbox for tool-schema trials.

The child process can import the evaluator and nothing else. It does not
receive Atlas credentials or API keys. A crash or timeout stays in the child.
The parent commits a schema to MongoDB only after this gate returns success.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent
WORKER_FLAG = "--worker"
TIMEOUT_SECONDS = 5

_SECRET_PREFIXES = ("MONGO", "OPENAI", "RAINDROP", "AWS", "DATABASE")


@dataclass
class SandboxResult:
    ok: bool
    message: str
    request: Optional[Dict[str, Any]]
    backend: str
    sandbox_id: str
    phase: str
    contained: bool
    secrets_stripped: bool
    gate: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "request": self.request,
            "backend": self.backend,
            "sandbox_id": self.sandbox_id,
            "phase": self.phase,
            "contained": self.contained,
            "secrets_stripped": self.secrets_stripped,
            "gate": self.gate,
        }


def _child_env() -> Dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HYDRA_SANDBOX_WORKER": "1",
    }


def _limit_child() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    except (ImportError, OSError, ValueError):
        return


def _secret_names() -> list:
    names = []
    for key in os.environ:
        if key.startswith(_SECRET_PREFIXES):
            names.append(key)
    return names


def _worker() -> int:
    _limit_child()
    try:
        job = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        json.dump({"ok": False, "message": "Sandbox rejected a malformed job."}, sys.stdout)
        return 0

    sys.path.insert(0, str(ROOT))
    import evaluator

    schema = job.get("schema")
    payload = job.get("payload")
    leaked = _secret_names()
    if leaked:
        json.dump(
            {
                "ok": False,
                "message": "Sandbox refused to run because credentials were visible.",
                "secrets_stripped": False,
            },
            sys.stdout,
        )
        return 0

    ok, message = evaluator.validate_tool_execution(schema, payload)
    request = evaluator.execution_request(schema, payload) if ok else None
    json.dump(
        {
            "ok": ok,
            "message": message,
            "request": request,
            "secrets_stripped": True,
        },
        sys.stdout,
    )
    return 0


def execute(schema: Dict[str, Any], payload: Dict[str, Any], *, phase: str) -> SandboxResult:
    """Run one schema trial in a fresh process. The caller decides whether to commit."""
    if os.environ.get("HYDRA_SANDBOX_WORKER") == "1":
        raise RuntimeError("Refusing to start a sandbox from inside a sandbox.")

    job = json.dumps({"schema": schema, "payload": payload})
    workspace = tempfile.mkdtemp(prefix="hydra-sandbox-")
    proc = subprocess.Popen(
        [sys.executable, "-I", str(ROOT / "sandbox.py"), WORKER_FLAG],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_env(),
        cwd=workspace,
        start_new_session=True,
        text=True,
    )
    sandbox_id = "pid {}".format(proc.pid)
    try:
        stdout, _stderr = proc.communicate(job, timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill(proc)
        return SandboxResult(
            ok=False,
            message="Sandbox contained a timed-out tool trial.",
            request=None,
            backend="process",
            sandbox_id=sandbox_id,
            phase=phase,
            contained=True,
            secrets_stripped=True,
        )

    if proc.returncode != 0:
        return SandboxResult(
            ok=False,
            message="Sandbox contained a crashed tool trial.",
            request=None,
            backend="process",
            sandbox_id=sandbox_id,
            phase=phase,
            contained=True,
            secrets_stripped=True,
        )

    try:
        body = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return SandboxResult(
            ok=False,
            message="Sandbox contained an unreadable tool trial.",
            request=None,
            backend="process",
            sandbox_id=sandbox_id,
            phase=phase,
            contained=True,
            secrets_stripped=True,
        )

    return SandboxResult(
        ok=bool(body.get("ok")),
        message=str(body.get("message") or "Sandbox returned no message."),
        request=body.get("request") if isinstance(body.get("request"), dict) else None,
        backend="process",
        sandbox_id=sandbox_id,
        phase=phase,
        contained=False,
        secrets_stripped=bool(body.get("secrets_stripped", True)),
    )


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        proc.kill()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        return


if __name__ == "__main__":
    if WORKER_FLAG in sys.argv:
        raise SystemExit(_worker())

    import evaluator

    broken = {"name": "query_collection", "parameters": {"resource": "customers"}}
    payload = {"resource": "customers", "endpoint": "/v1/customers"}
    failed = execute(broken, payload, phase="self-check")
    assert not failed.ok, failed
    assert failed.secrets_stripped
    assert failed.message == "Missing required tool parameter: api_version"
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
    passed = execute(repaired, payload, phase="self-check")
    assert passed.ok, passed
    assert passed.message == evaluator.SUCCESS_MESSAGE
    assert passed.request and passed.request["endpoint"] == "/v1/customers"
    print("{} {}".format(passed.backend, passed.sandbox_id))
    print(passed.message)
