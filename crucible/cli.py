"""Crucible CLI (specs.md §14: `crucible run --repo ...`, `crucible status`)."""

from __future__ import annotations

import uuid
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="Vulnerability Discovery Harness — Phase 1")


@app.command()
def run(
    repo: Path = typer.Option(..., "--repo", exists=True, file_okay=False, help="read-only checkout"),
    workspace: Path = typer.Option(Path(".crucible-workspace"), "--workspace"),
    checkpoint_db: Path = typer.Option(Path("checkpoints.sqlite"), "--checkpoint-db"),
    resume: str = typer.Option("", "--resume", help="run_id to resume from checkpoint"),
) -> None:
    """Recon -> Hunt -> Validate -> Report, end to end (§14.1)."""
    from crucible.graph.build import build_graph
    from crucible.workspace.fs import init_workspace

    run_id = resume or uuid.uuid4().hex[:12]
    init_workspace(workspace)
    graph = build_graph(checkpoint_db)

    config = {"configurable": {"thread_id": run_id}}
    initial = {
        "run_id": run_id,
        "repo_path": str(repo),
        "workspace_path": str(workspace),
        "repo_commit": "",           # TODO: git rev-parse in repo
        "primary_language": "",      # TODO: detect -> noise budget (§12)
        "architecture_path": "",
        "taxonomy_path": "",
        "pending_hunts": [],
        "completed_cells": [],
        "finding_ids": [],
        "fork_count": 0,
        "continuation_count": 0,
        "token_spend": 0,
    }
    typer.echo(f"run_id={run_id}")
    graph.invoke(initial, config=config)  # resume: LangGraph reloads from checkpoint


@app.command()
def status(
    run_id: str = typer.Argument(...),
    store_url: str = typer.Option("sqlite:///findings.sqlite", "--store-url"),
) -> None:
    """Report fork rate and per-tool invocation counts (§14.10)."""
    from crucible.store.dao import Store

    store = Store(store_url)
    usage = store.tool_usage(run_id)
    if not usage:
        typer.echo("no tool-usage rows for this run")
        raise typer.Exit(1)
    typer.echo(f"{'role':<16}{'tool':<18}{'count':>8}{'errors':>8}")
    for u in usage:
        typer.echo(f"{u.role:<16}{u.tool_name:<18}{u.count:>8}{u.errors:>8}")


if __name__ == "__main__":
    app()
