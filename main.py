"""Live CLI demo: a repaired tool contract transfers from Task A to Task B."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from pymongo.errors import PyMongoError
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from hydra_harness import database, harness

ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hydra-Harness transferable learning demo"
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Skip the pauses used for a live stage demo",
    )
    return parser.parse_args()


def pause(enabled: bool, seconds: float = 0.65) -> None:
    if enabled:
        time.sleep(seconds)


def json_block(payload: Any) -> Syntax:
    return Syntax(json.dumps(payload, indent=2), "json", theme="monokai", word_wrap=True)


def step(console: Console, number: str, title: str) -> None:
    console.print()
    console.rule("[bold bright_cyan]{}   {}[/]".format(number, title))


def shutdown() -> None:
    harness.memory.close()
    database.close()


def fail(console: Console, title: str, message: str) -> None:
    console.print(Panel(escape(database.mask_uri(message)), title=title, border_style="red"))


def banner(console: Console) -> None:
    body = (
        "[bold bright_cyan]HYDRA-HARNESS[/]\n"
        "[white]Recursive harnessing for tool contracts[/]\n\n"
        "[dim]Problem Statement[/]   Recursive Harnessing\n"
        "[dim]Cerebral Valley[/]   SF Tech Week\n\n"
        "Task A fails, the outer loop repairs the schema, and a v2 guardrail\n"
        "rejects a call that has no idempotency key. Task B inherits that\n"
        "contract from MongoDB Atlas on the first attempt."
    )
    console.print(Panel(body, border_style="bright_cyan", padding=(1, 4)))


def schema_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Table:
    table = Table(title="schema diff  ·  parameters", header_style="bold magenta", show_lines=False)
    table.add_column("change")
    table.add_column("parameter")
    table.add_column("v1", style="red")
    table.add_column("v2", style="green")
    left = dict(before.get("parameters") or {})
    right = dict(after.get("parameters") or {})
    for name in list(dict.fromkeys(list(left) + list(right))):
        old = left.get(name)
        new = right.get(name)
        if name not in left:
            table.add_row("[green]added[/]", name, "—", json.dumps(new))
        elif name not in right:
            table.add_row("[red]removed[/]", name, json.dumps(old), "—")
        elif old != new:
            table.add_row("[yellow]changed[/]", name, json.dumps(old), json.dumps(new))
        else:
            table.add_row("[dim]kept[/]", name, json.dumps(old), json.dumps(new))
    return table


def side_by_side(before: Dict[str, Any], after: Dict[str, Any], version_after: int) -> Table:
    left = Panel(
        json_block(before),
        title="[bold red]Schema v1[/]",
        border_style="red",
        padding=(1, 1),
    )
    right = Panel(
        json_block(after),
        title="[bold green]Schema v{}[/]".format(version_after),
        border_style="green",
        padding=(1, 1),
    )
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(left, right)
    return grid


def request_panel(title: str, request: Optional[Dict[str, Any]], ok: bool) -> Panel:
    body = json_block(request or {})
    style = "green" if ok else "red"
    label = "[green]PASS[/]" if ok else "[red]FAIL[/]"
    return Panel(body, title="{}   {}".format(label, title), border_style=style)


def summary_table(documents: List[Dict[str, Any]]) -> Table:
    table = Table(
        title="MongoDB Atlas  ·  hydra_harness.agent_states",
        header_style="bold bright_cyan",
        show_lines=False,
    )
    table.add_column("version", justify="right")
    table.add_column("status")
    table.add_column("parameters")
    table.add_column("guardrail")
    table.add_column("diagnosis")
    table.add_column("created")
    table.add_column("atlas _id")
    for document in documents:
        version = int(document["version"])
        status = "repaired" if version > 1 else "baseline"
        style = "green" if version > 1 else "yellow"
        parameters = document.get("tool_schema", {}).get("parameters", {})
        created = str(document.get("created_at", ""))
        if "T" in created:
            clock = created.split("T", 1)[1].split(".", 1)[0].split("+", 1)[0]
            created = clock + " UTC"
        diagnosis = str(document.get("diagnosis") or "")
        if len(diagnosis) > 72:
            diagnosis = diagnosis[:69] + "..."
        guardrail = "idempotency" if document.get("guardrails") else "—"
        table.add_row(
            "v{}".format(version),
            "[{}]{}[/]".format(style, status),
            ", ".join(parameters.keys()) or "—",
            guardrail,
            escape(diagnosis),
            created or "—",
            str(document.get("_id", "")),
        )
    return table


def show_sandbox(console: Console, runs: list) -> None:
    if not runs:
        return
    for run in runs:
        status = "pass" if run.get("ok") else "contained" if run.get("contained") else "fail"
        color = "green" if run.get("ok") else "yellow"
        console.print(
            "[{color}]SANDBOX[/]  {backend}  {sandbox_id}  {phase}  {status}  secrets stripped".format(
                color=color,
                status=status,
                **run,
            )
        )


def show_cost(console: Console, label: str, report: harness.HarnessReport) -> None:
    tokens = report.prompt_tokens + report.completion_tokens
    console.print(
        "[bold]COST[/]  {}   model calls {}   tokens ~{}".format(
            label, report.model_calls, tokens
        )
    )


def show_rejections(console: Console, report: harness.HarnessReport) -> None:
    patches = report.rejected_patches
    if not patches:
        return
    patch = patches[0]
    schema = patch.get("schema") or {}
    parameters = schema.get("parameters") if isinstance(schema.get("parameters"), dict) else {}
    body = {
        "reason": patch.get("reason"),
        "tool": schema.get("name"),
        "parameters": parameters,
        "guardrails": patch.get("guardrails") or [],
    }
    console.print(
        Panel(
            json_block(body),
            title="[yellow]REJECTED[/]   patch held before commit",
            border_style="yellow",
        )
    )
    if len(patches) > 1:
        console.print(
            "[dim]{} more rejected patch{} held before commit[/]".format(
                len(patches) - 1, "" if len(patches) == 2 else "es"
            )
        )


def show_task_a(console: Console, report: harness.HarnessReport, cinematic: bool) -> None:
    step(console, "01", "TASK A   FAILURE & SELF-REPAIR")
    console.print(
        "[bold]{}[/]   [dim]{}[/]   loaded [yellow]v{}[/] from Atlas".format(
            report.title, report.endpoint, report.version_loaded
        )
    )
    pause(cinematic, 0.35)
    console.print(
        Panel(
            escape(report.failure_message or "tool execution failed"),
            title="[red]FAIL[/]   attempt #1",
            border_style="red",
        )
    )
    for span in report.spans:
        console.print(
            "[red]TRACE[/]  {span}  v{version}  backend [bold]{backend}[/]".format(**span)
        )
        console.print("        [dim]{}[/]".format(escape(span.get("error", ""))))
    console.print("[dim]{}[/]".format(escape(harness.telemetry().note)))

    pause(cinematic, 0.45)
    if harness.TASK_A.get("recall") is False:
        console.print(
            "[dim]MEMORY  skipped for Task A so this run shows a first encounter. "
            "The model has to discover the repair.[/]"
        )
    proposer = report.proposer or "deterministic"
    if proposer == "atlas-vector-search":
        match = report.vector_match or {}
        console.print(
            "[bright_cyan]VECTOR[/]  Atlas Vector Search  score {:.2f}  prior agent {}".format(
                float(match.get("score") or 0.0),
                escape(str(match.get("agent_id") or "")),
            )
        )
        console.print("        [dim]{}[/]".format(escape(str(match.get("trace_text") or ""))))
        console.print("[magenta]OUTER LOOP[/]  reused the stored repair   model calls 0")
    elif proposer == "deterministic":
        console.print("[magenta]OUTER LOOP[/]  [bold]deterministic fallback, last resort[/]")
    else:
        console.print("[magenta]OUTER LOOP[/]  model [bold]{}[/]".format(escape(proposer)))
    if report.proposer_note:
        console.print("[dim]{}[/]".format(escape(report.proposer_note)))
    if report.diagnosis:
        console.print(Panel(escape(report.diagnosis), title="diagnosis", border_style="magenta"))
    show_rejections(console, report)
    show_sandbox(console, report.sandbox_runs)
    console.print(
        "[green]EVALUATOR[/]  {}".format(escape(report.message))
    )
    console.print(
        "[green]COMMIT[/]  Atlas accepted v{} only after the sandbox passed  _id [bold]{}[/]".format(
            report.version_saved, report.document_id
        )
    )
    show_cost(console, "Task A repair", report)
    if report.guardrails:
        console.print(
            "[green]GUARDRAIL[/]  v{} stores [bold]{}[/]".format(
                report.version_saved, escape(report.guardrails[0])
            )
        )
    console.print()
    if report.schema_after is not None and report.version_saved is not None:
        console.print(side_by_side(report.schema_before, report.schema_after, report.version_saved))
        console.print(schema_diff(report.schema_before, report.schema_after))
    console.print(request_panel("sandbox verify  {}".format(report.endpoint), report.request, True))


def show_recall(console: Console, report: harness.HarnessReport) -> None:
    step(console, "02", "VECTOR RECALL   SAME FAILURE, DIFFERENT WORDS")
    console.print(
        "[bold]{}[/]   [dim]{}[/]".format(report.title, report.endpoint)
    )
    console.print(
        "The invoices error is phrased differently from the customers error. "
        "Atlas Vector Search returns the repair Task A stored."
    )
    show_task_a_vector = report.vector_match or {}
    console.print(
        "[bright_cyan]VECTOR[/]  score {:.2f}  stored by {}".format(
            float(show_task_a_vector.get("score") or 0.0),
            escape(str(show_task_a_vector.get("agent_id") or "")),
        )
    )
    console.print(
        "        [dim]{}[/]".format(escape(str(show_task_a_vector.get("trace_text") or "")))
    )
    show_sandbox(console, report.sandbox_runs)
    console.print(
        "[green]COMMIT[/]  Atlas accepted v{} only after the sandbox passed".format(
            report.version_saved
        )
    )
    show_cost(console, "invoice repair", report)


def show_guardrail(
    console: Console,
    blocked: harness.HarnessReport,
    allowed: harness.HarnessReport,
) -> None:
    step(console, "03", "GUARDRAIL   v2 ONLY")
    console.print(
        "v1 stored no guardrail. v2 rejects a customers call that omits the key."
    )
    show_sandbox(console, blocked.sandbox_runs)
    console.print(
        Panel(
            escape(blocked.message),
            title="[red]FAIL[/]   customers without idempotency_key",
            border_style="red",
        )
    )
    show_cost(console, "blocked call", blocked)
    show_sandbox(console, allowed.sandbox_runs)
    console.print(
        Panel(
            escape(allowed.message),
            title="[green]PASS[/]   same call with idempotency_key",
            border_style="green",
        )
    )
    show_cost(console, "allowed call", allowed)
    console.print(request_panel("guardrail pass  {}".format(allowed.endpoint), allowed.request, True))


def show_index(console: Console, agent_id: str) -> None:
    try:
        plan = database.explain_latest(agent_id)
    except PyMongoError:
        return
    console.print(
        "[bright_cyan]INDEX[/]  {stage}  [bold]{index}[/]  (agent_id 1, version -1)  "
        "keys examined {keys}  docs examined {docs}  {ms} ms".format(
            stage=plan["stage"],
            index=plan["index"] or "none",
            keys=plan["keys_examined"],
            docs=plan["docs_examined"],
            ms=plan["millis"],
        )
    )


def show_task_b(console: Console, report: harness.HarnessReport, agent_id: str) -> None:
    step(console, "04", "TASK B   TRANSFERABLE SUCCESS")
    console.print(
        "[bold]{}[/]   [dim]{}[/]".format(report.title, report.endpoint)
    )
    console.print(
        "Loaded [green]v{}[/] directly from Atlas. Repair loop skipped.".format(
            report.version_loaded
        )
    )
    show_index(console, agent_id)
    show_sandbox(console, report.sandbox_runs)
    if report.ok:
        console.print(
            Panel(
                "{}\n\nattempt #1   no mutation".format(escape(report.message)),
                title="[green]PASS[/]",
                border_style="green",
            )
        )
        console.print(request_panel("inherited contract  {}".format(report.endpoint), report.request, True))
        show_cost(console, "Task B", report)
        return
    console.print(
        Panel(
            escape(report.failure_message or report.message),
            title="[red]FAIL[/]   attempt #1",
            border_style="red",
        )
    )


def show_summary(console: Console, agent_id: str, documents: List[Dict[str, Any]]) -> None:
    step(console, "05", "ATLAS VERIFICATION")
    console.print(summary_table(documents))
    versions = ", ".join("v{}".format(item["version"]) for item in documents)
    console.print(
        "[green]STORED[/]  agent [bold]{}[/]  versions {}".format(agent_id, versions)
    )
    console.print(
        Panel(
            "Atlas  →  Browse Collections  →  [bold]hydra_harness[/]\n"
            "collection  [bold]agent_states[/]   and   [bold]error_traces[/]\n"
            "agent_id  [bold]{}[/]\n\n"
            "Task A wrote the repair and embedded the error.\n"
            "A differently worded invoices failure reused it from Atlas Vector Search.\n"
            "Task B read v2 back and succeeded on attempt #1 with no model call.".format(escape(agent_id)),
            title="Show the judges",
            border_style="bright_cyan",
        )
    )


def run_demo(console: Console, cinematic: bool) -> int:
    banner(console)
    load_dotenv(ROOT / ".env")

    try:
        with console.status("[cyan]Pinging MongoDB Atlas…", spinner="dots"):
            _, database_handle = database.connect()
            pause(cinematic, 0.4)
    except (PyMongoError, RuntimeError, OSError) as exc:
        fail(
            console,
            "Atlas unreachable",
            "{}\n\nIn Atlas, open Network Access and allow this machine, "
            "then run the demo again.".format(exc),
        )
        return 1

    host = database.atlas_host(os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI", ""))
    console.print(
        "[green]LINKED[/]  [bold]{}[/]   db [bold]{}[/]   collection [bold]{}[/]".format(
            host, database_handle.name, database.COLLECTION_NAME
        )
    )

    agent_id = "hydra-{}".format(secrets.token_hex(3))
    console.print("[dim]agent_id[/]  {}".format(agent_id))
    traces = harness.telemetry()
    console.print("[dim]telemetry[/]  {}".format(traces.backend))
    with console.status("[cyan]Preparing Atlas Vector Search…", spinner="dots"):
        memory_ready = True
        try:
            harness.memory.ensure_index()
        except Exception as exc:
            memory_ready = False
            fail(console, "Atlas Vector Search unavailable", str(exc))
            shutdown()
            harness.memory.close()
            return 1
    if memory_ready:
        console.print("[green]VECTOR[/]  index [bold]error_trace_vector[/]  on error_traces")

    with console.status("[yellow]Task A  ·  executing schema v1…", spinner="dots"):
        started = time.perf_counter()
        task_a = harness.run_task(agent_id, harness.TASK_A, repair=True)
        elapsed_a = (time.perf_counter() - started) * 1000
    show_task_a(console, task_a, cinematic)
    console.print("[dim]harness time  {:.0f} ms[/]".format(elapsed_a))
    if not task_a.repaired or not task_a.ok:
        fail(console, "Task A did not repair", task_a.failure_message or task_a.message)
        shutdown()
        harness.memory.close()
        return 1

    recall_agent = "hydra-{}".format(secrets.token_hex(3))
    with console.status("[cyan]Vector recall  ·  waiting for the index, then listing invoices…", spinner="dots"):
        harness.memory.wait_until_queryable()
        recall = harness.run_task(recall_agent, harness.TASK_RECALL, repair=True)
    show_recall(console, recall)
    if (
        not recall.ok
        or recall.proposer != "atlas-vector-search"
        or recall.model_calls != 0
    ):
        fail(
            console,
            "Vector recall did not reuse a past repair",
            recall.failure_message or recall.message,
        )
        shutdown()
        harness.memory.close()
        return 1

    customers_with_key = {
        "id": "A2",
        "title": "List customers with an idempotency key",
        "endpoint": harness.TASK_A["endpoint"],
        "payload": dict(harness.TASK_A["payload"], idempotency_key="cust-replay-1"),
    }
    with console.status("[yellow]Guardrail  ·  replaying customers…", spinner="dots"):
        blocked = harness.run_task(agent_id, harness.TASK_A, repair=False)
        allowed = harness.run_task(agent_id, customers_with_key, repair=False)
    show_guardrail(console, blocked, allowed)
    if blocked.ok or blocked.model_calls != 0:
        fail(console, "Guardrail did not reject the bare call", blocked.message)
        shutdown()
        return 1
    if not allowed.ok or allowed.model_calls != 0:
        fail(console, "Guardrail did not allow the keyed call", allowed.message)
        shutdown()
        return 1

    with console.status("[green]Task B  ·  loading the repaired contract…", spinner="dots"):
        started = time.perf_counter()
        task_b = harness.run_task(agent_id, harness.TASK_B, repair=False)
        elapsed_b = (time.perf_counter() - started) * 1000
        pause(cinematic, 0.35)
    show_task_b(console, task_b, agent_id)
    console.print("[dim]harness time  {:.0f} ms[/]".format(elapsed_b))
    if not task_b.ok or task_b.version_loaded < 2 or task_b.model_calls != 0:
        fail(console, "Task B did not inherit v2", task_b.message)
        shutdown()
        return 1
    console.print(
        "Task A spent {} model call{} and ~{} tokens on the repair. "
        "Task B spent 0 model calls because the harness already changed.".format(
            task_a.model_calls,
            "" if task_a.model_calls == 1 else "s",
            task_a.prompt_tokens + task_a.completion_tokens,
        )
    )

    documents = database.list_states(agent_id)
    show_summary(console, agent_id, documents)
    if len(documents) < 2:
        fail(console, "Atlas verification failed", "Expected version 1 and version 2.")
        shutdown()
        return 1

    traces.shutdown()
    shutdown()
    return 0


def main() -> None:
    args = parse_args()
    console = Console()
    try:
        code = run_demo(console, cinematic=not args.fast)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/]")
        shutdown()
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
