"""CLI smoke — the full pipeline runs end to end deterministically with no model
reachable: Recon degrades to seed_only, Hunt/validate passes find nothing, and
`report.json` / `report.md` are still emitted. This is the issue #17 plumbing
acceptance (a model-backed real-bug run is issue #3)."""

from __future__ import annotations

import json
import sqlite3

from typer.testing import CliRunner

from crucible.cli import app
from crucible.recon.decompose import ARCHITECTURE_SECTIONS

runner = CliRunner()

_HERMETIC_ENV = (
    "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "RECON_LLM", "HUNTER_LLM",
    "VALIDATOR_BUG_LLM", "VALIDATOR_REACH_LLM",
    "LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGCHAIN_TRACING_V2",
    "CRUCIBLE_OTEL", "OTEL_EXPORTER_OTLP_ENDPOINT",
)


def test_help():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "run" in r.output and "status" in r.output


def test_run_completes_end_to_end_and_emits_report(tmp_path, monkeypatch, repo_web):
    # Hermetic no-model path: default endpoints point at a local Ollama that
    # isn't running -> connection refused -> logged, every stage continues.
    # run from a clean dir so the repo's own .env (LangSmith key, DEEPSEEK_API_KEY)
    # never loads — the run must be fully hermetic
    monkeypatch.chdir(tmp_path)
    for var in _HERMETIC_ENV:
        monkeypatch.delenv(var, raising=False)
    # force every ollama role at a dead endpoint so a locally-running Ollama
    # can't turn this into a real (slow) model run
    monkeypatch.setenv("CRUCIBLE_OLLAMA_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("CRUCIBLE_HUNT_MAX_TASKS", "1")
    monkeypatch.setenv("CRUCIBLE_HUNT_EXPLORE_LIMIT", "2")
    monkeypatch.setenv("CRUCIBLE_MAX_CYCLES", "1")
    monkeypatch.setenv("CRUCIBLE_RECON_MAX_PARALLEL", "1")

    store = tmp_path / "findings.sqlite"
    ws = tmp_path / "ws"
    r = runner.invoke(app, [
        "run",
        "--repo", str(repo_web),
        "--workspace", str(ws),
        "--checkpoint-db", str(tmp_path / "ckpt.sqlite"),
        "--store-url", f"sqlite:///{store}",
        "--no-sandbox",
    ])

    assert r.exit_code == 0, r.output
    assert "recon_quality=seed_only" in r.output
    assert "stopped at stub node" not in r.output

    # Recon artifacts
    assert (ws / ".git").is_dir()
    seed = json.loads((ws / "recon" / "seed.json").read_text())
    assert seed["primary_language"] == "python"

    arch = (ws / "architecture.md").read_text()
    for header in ARCHITECTURE_SECTIONS:
        assert header in arch, f"missing {header}"
    assert "Recon degradation — `seed_only`" in arch
    assert isinstance(json.loads((ws / "recon" / "attack_surface.json").read_text()), list)

    manifest = json.loads((ws / "recon" / "task_manifest.json").read_text())
    assert manifest["count"] >= 1
    classes = {c["attack_class"] for c in manifest["chunks"]}
    assert not (classes & {"memory_oob_write", "use_after_free"})  # pruned for python

    # Report — the pipeline reached the end and rendered.
    report = json.loads((ws / "report.json").read_text())
    assert report["run_id"] and report["language"] == "py"
    assert report["counts"]["total"] == 0 and report["counts"]["upheld"] == 0
    assert report["findings"] == []
    assert "metrics" in report and "tool_usage" in report["metrics"]
    md = (ws / "report.md").read_text()
    assert md.startswith("# Security report")
    assert "None survived" in md

    rows = list(sqlite3.connect(store).execute("select primary_language, status from runs"))
    assert rows and rows[0][0] == "py"


def test_run_id_flag_fills_in_a_pre_registered_row(tmp_path, monkeypatch, repo_web):
    """The API launcher (issue #57) inserts a `Run` row before the repo is even
    cloned, then invokes `crucible run --run-id <id>`. The CLI must update that
    row in place rather than fail on a duplicate insert."""
    monkeypatch.chdir(tmp_path)
    for var in _HERMETIC_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CRUCIBLE_OLLAMA_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("CRUCIBLE_RECON_MAX_PARALLEL", "1")

    from crucible.store.dao import Store

    store_path = tmp_path / "findings.sqlite"
    store = Store(f"sqlite:///{store_path}")
    store.create_launch("abc123", "octocat/Hello-World")

    ws = tmp_path / "ws"
    r = runner.invoke(app, [
        "run",
        "--repo", str(repo_web),
        "--workspace", str(ws),
        "--checkpoint-db", str(tmp_path / "ckpt.sqlite"),
        "--store-url", f"sqlite:///{store_path}",
        "--run-id", "abc123",
        "--no-sandbox",
        "--stop-after", "recon",
    ])

    assert r.exit_code == 3, r.output  # clean --stop-after exit
    assert "run_id=abc123" in r.output

    row = store.get_run("abc123")
    assert row is not None
    assert row.repo_path == str(repo_web)
    assert row.clone_status == "pending"  # untouched — the CLI only fills in repo info
    rows = list(sqlite3.connect(store_path).execute("select run_id from runs"))
    assert rows == [("abc123",)]  # no duplicate row


def test_run_id_and_resume_are_mutually_exclusive():
    r = runner.invoke(app, ["run", "--repo", ".", "--run-id", "a", "--resume", "b"])
    assert r.exit_code != 0
    assert "not both" in r.output
