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


def fail(console: Console, title: str, message: str) -> None:
    console.print(Panel(escape(database.mask_uri(message)), title=title, border_style="red"))


def banner(console: Console) -> None:
    body = (
        "[bold bright_cyan]HYDRA-HARNESS[/]\n"
        "[white]Recursive harnessing for tool contracts[/]\n\n"
        "[dim]Problem Statement[/]   Recursive Harnessing\n"
        "[dim]Cerebral Valley[/]   SF Tech Week\n\n"
        "Task A fails, the outer loop repairs the schema, and Task B\n"
        "inherits that contract from MongoDB Atlas on the first attempt."
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
        table.add_row(
            "v{}".format(version),
            "[{}]{}[/]".format(style, status),
            ", ".join(parameters.keys()) or "—",
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
    proposer = report.proposer or "deterministic"
    if proposer == "deterministic":
        console.print("[magenta]OUTER LOOP[/]  [bold]deterministic fallback[/]")
    else:
        console.print("[magenta]OUTER LOOP[/]  model [bold]{}[/]".format(escape(proposer)))
    if report.proposer_note:
        console.print("[dim]{}[/]".format(escape(report.proposer_note)))
    if report.diagnosis:
        console.print(Panel(escape(report.diagnosis), title="diagnosis", border_style="magenta"))
    show_sandbox(console, report.sandbox_runs)
    console.print(
        "[green]EVALUATOR[/]  {}".format(escape(report.message))
    )
    console.print(
        "[green]COMMIT[/]  Atlas accepted v{} only after the sandbox passed  _id [bold]{}[/]".format(
            report.version_saved, report.document_id
        )
    )
    console.print()
    if report.schema_after is not None and report.version_saved is not None:
        console.print(side_by_side(report.schema_before, report.schema_after, report.version_saved))
        console.print(schema_diff(report.schema_before, report.schema_after))
    console.print(request_panel("repaired call  {}".format(report.endpoint), report.request, True))


def show_task_b(console: Console, report: harness.HarnessReport) -> None:
    step(console, "02", "TASK B   TRANSFERABLE SUCCESS")
    console.print(
        "[bold]{}[/]   [dim]{}[/]".format(report.title, report.endpoint)
    )
    console.print(
        "Loaded [green]v{}[/] directly from Atlas. Repair loop skipped.".format(
            report.version_loaded
        )
    )
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
        return
    console.print(
        Panel(
            escape(report.failure_message or report.message),
            title="[red]FAIL[/]   attempt #1",
            border_style="red",
        )
    )


def show_summary(console: Console, agent_id: str, documents: List[Dict[str, Any]]) -> None:
    step(console, "03", "ATLAS VERIFICATION")
    console.print(summary_table(documents))
    versions = ", ".join("v{}".format(item["version"]) for item in documents)
    console.print(
        "[green]STORED[/]  agent [bold]{}[/]  versions {}".format(agent_id, versions)
    )
    console.print(
        Panel(
            "Atlas  →  Browse Collections  →  [bold]hydra_harness[/]\n"
            "collection  [bold]agent_states[/]\n"
            "agent_id  [bold]{}[/]\n\n"
            "Task A wrote the repair. Task B read it back and succeeded\n"
            "on attempt #1.".format(escape(agent_id)),
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

    with console.status("[yellow]Task A  ·  executing schema v1…", spinner="dots"):
        started = time.perf_counter()
        task_a = harness.run_task(agent_id, harness.TASK_A, repair=True)
        elapsed_a = (time.perf_counter() - started) * 1000
    show_task_a(console, task_a, cinematic)
    console.print("[dim]harness time  {:.0f} ms[/]".format(elapsed_a))
    if not task_a.repaired or not task_a.ok:
        fail(console, "Task A did not repair", task_a.failure_message or task_a.message)
        database.close()
        return 1

    with console.status("[green]Task B  ·  loading the repaired contract…", spinner="dots"):
        started = time.perf_counter()
        task_b = harness.run_task(agent_id, harness.TASK_B, repair=False)
        elapsed_b = (time.perf_counter() - started) * 1000
        pause(cinematic, 0.35)
    show_task_b(console, task_b)
    console.print("[dim]harness time  {:.0f} ms[/]".format(elapsed_b))
    if not task_b.ok or task_b.version_loaded < 2:
        fail(console, "Task B did not inherit v2", task_b.message)
        database.close()
        return 1

    documents = database.list_states(agent_id)
    show_summary(console, agent_id, documents)
    if len(documents) < 2:
        fail(console, "Atlas verification failed", "Expected version 1 and version 2.")
        database.close()
        return 1

    traces.shutdown()
    database.close()
    return 0


def main() -> None:
    args = parse_args()
    console = Console()
    try:
        code = run_demo(console, cinematic=not args.fast)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/]")
        database.close()
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
