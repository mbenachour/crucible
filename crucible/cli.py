"""Crucible CLI (specs.md §14: `crucible run --repo ...`, `crucible status`)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="Crucible — vulnerability discovery harness (Phase 1)")


def _load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


@app.command()
def run(
    repo: Path = typer.Option(..., "--repo", exists=True, file_okay=False, help="read-only checkout"),
    workspace: Path = typer.Option(Path(".crucible-workspace"), "--workspace"),
    checkpoint_db: Path = typer.Option(Path("checkpoints.sqlite"), "--checkpoint-db"),
    store_url: str = typer.Option("sqlite:///findings.sqlite", "--store-url"),
    config: str = typer.Option("", "--config", help="path to crucible.toml"),
    resume: str = typer.Option("", "--resume", help="run_id to resume from checkpoint"),
    no_sandbox: bool = typer.Option(False, "--no-sandbox", help="skip Docker sandbox boot check"),
) -> None:
    """Recon -> Hunt -> Validate -> Report, end to end (§14.1)."""
    import time

    _load_dotenv()
    from crucible.config import load_registry
    from crucible.graph.build import build_graph
    from crucible.graph.deps import NodeDeps
    from crucible.llm.registry import ModelRole
    from crucible.obs import configure_logging, setup_tracing, span
    from crucible.repo import git_commit, primary_language
    from crucible.store.dao import Store
    from crucible.workspace.fs import init_workspace

    run_id = resume or uuid.uuid4().hex[:12]
    init_workspace(workspace)

    log = configure_logging(workspace)
    setup_tracing()  # opt-in via CRUCIBLE_OTEL / OTEL_EXPORTER_OTLP_ENDPOINT
    started = time.monotonic()

    registry = load_registry(config or None)
    store = Store(store_url)

    sandbox_provider = None
    if not no_sandbox:
        from crucible.sandbox.docker import DockerSandboxProvider, assert_boot_environment

        assert_boot_environment()  # fail loudly on the nested-container trap (§10)
        sandbox_provider = DockerSandboxProvider()

    repo_commit = git_commit(repo)
    language = primary_language(repo)
    log.info(
        "run %s %s  repo=%s  commit=%s  language=%s  sandbox=%s",
        "resume" if resume else "start", run_id, repo, repo_commit[:12] or "(none)",
        language, "off" if no_sandbox else "docker",
    )
    for role in ModelRole:
        ep = registry.endpoint(role)
        log.debug("model[%s] = %s:%s", role.value, ep.provider.value, ep.model)

    if not resume:
        store.create_run(run_id, str(repo), repo_commit, language)

    deps = NodeDeps(
        registry=registry,
        store=store,
        sandbox_provider=sandbox_provider,
        config_path=config or None,
    )
    graph = build_graph(deps, checkpoint_db)

    initial = {
        "run_id": run_id,
        "repo_path": str(repo),
        "workspace_path": str(workspace),
        "repo_commit": repo_commit,
        "primary_language": language,
        "architecture_path": "",
        "taxonomy_path": "",
        "pending_hunts": [],
        "completed_cells": [],
        "finding_ids": [],
        "fork_count": 0,
        "continuation_count": 0,
        "cycle_count": 0,
        "token_spend": 0,
    }
    typer.echo(f"run_id={run_id}  commit={repo_commit[:12] or '(none)'}  language={language}")
    from crucible.graph.hooks import graph_recursion_limit

    try:
        with span("crucible.run", run_id=run_id, repo=str(repo), language=language):
            graph.invoke(
                initial,
                config={
                    "configurable": {"thread_id": run_id},
                    "recursion_limit": graph_recursion_limit(),
                },
            )
    except NotImplementedError as e:
        # Phase 1: pipeline nodes are still stubs. Everything up to the node
        # boundary (config, registry, store, workspace, checkpointer) ran.
        elapsed = time.monotonic() - started
        log.warning("run %s stopped at stub node after %.1fs: %s", run_id, elapsed, e)
        typer.secho(f"stopped at stub node: {e}", fg=typer.colors.YELLOW)
        typer.echo(f"resume after implementing it with:  crucible run --repo {repo} --resume {run_id}")
        typer.echo(f"logs: {workspace}/run.log")
        raise typer.Exit(3)
    except Exception:
        log.exception("run %s failed after %.1fs", run_id, time.monotonic() - started)
        raise
    log.info("run %s complete in %.1fs", run_id, time.monotonic() - started)


@app.command()
def status(
    run_id: str = typer.Argument(...),
    store_url: str = typer.Option("sqlite:///findings.sqlite", "--store-url"),
) -> None:
    """Report fork rate and per-tool invocation counts (§14.10)."""
    _load_dotenv()
    from crucible.store.dao import Store

    store = Store(store_url)
    usage = store.tool_usage(run_id)
    if not usage:
        typer.echo("no tool-usage rows for this run")
        raise typer.Exit(1)

    forks = sum(u.count for u in usage if u.tool_name == "fork_sibling")
    hunts = sum(u.count for u in usage if u.role == "hunter" and u.tool_name in ("bash", "sandbox_exec"))
    typer.echo(f"fork rate (forks / hunt-exec calls): {forks}/{hunts}")
    typer.echo(f"{'role':<16}{'tool':<18}{'count':>8}{'errors':>8}")
    for u in usage:
        typer.echo(f"{u.role:<16}{u.tool_name:<18}{u.count:>8}{u.errors:>8}")


if __name__ == "__main__":
    app()
