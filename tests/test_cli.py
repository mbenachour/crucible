"""CLI smoke — Recon runs end to end deterministically, then the pipeline stops
at the first remaining stub (validate_bug). No model is reachable, so R1/R2 fail
gracefully and recon_quality degrades to seed_only (issue #34)."""

from __future__ import annotations

import json
import sqlite3

from typer.testing import CliRunner

from crucible.cli import app
from crucible.recon.decompose import ARCHITECTURE_SECTIONS

runner = CliRunner()


def test_help():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "run" in r.output and "status" in r.output


def test_run_completes_recon_then_stops_at_validate_stub(tmp_path, monkeypatch, repo_web):
    # Force the hermetic no-model path: default endpoints point at a local
    # Ollama that isn't running -> connection refused -> logged, run continues.
    for var in ("DEEPSEEK_API_KEY", "RECON_LLM", "HUNTER_LLM"):
        monkeypatch.delenv(var, raising=False)
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

    assert r.exit_code == 3, r.output
    assert "stopped at stub node: validate_bug" in r.output
    assert "recon_quality=seed_only" in r.output

    # Recon artifacts
    assert (ws / ".git").is_dir()
    seed = json.loads((ws / "recon" / "seed.json").read_text())
    assert seed["primary_language"] == "python"

    arch = (ws / "architecture.md").read_text()
    for header in ARCHITECTURE_SECTIONS:
        assert header in arch, f"missing {header}"
    assert "Recon degradation — `seed_only`" in arch

    surface = json.loads((ws / "recon" / "attack_surface.json").read_text())
    assert isinstance(surface, list)
    assert (ws / "recon" / "recon_quality.txt").read_text().strip() == "seed_only"

    manifest = json.loads((ws / "recon" / "task_manifest.json").read_text())
    assert manifest["count"] >= 1
    classes = {c["attack_class"] for c in manifest["chunks"]}
    assert not (classes & {"memory_oob_write", "use_after_free"})  # pruned for python

    rows = list(sqlite3.connect(store).execute("select primary_language, status from runs"))
    assert rows and rows[0][0] == "py"
