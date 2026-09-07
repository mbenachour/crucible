"""CLI smoke tests — everything up to the (still-stubbed) pipeline nodes."""

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from crucible.cli import app

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "repos" / "fixture-py"


def test_help():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "run" in r.output and "status" in r.output


def test_status_no_rows(tmp_path):
    r = runner.invoke(app, ["status", "nope", "--store-url", f"sqlite:///{tmp_path/'f.sqlite'}"])
    assert r.exit_code == 1
    assert "no tool-usage rows" in r.output


def test_run_completes_recon_then_stops_at_hunt_stub(tmp_path):
    store = tmp_path / "findings.sqlite"
    r = runner.invoke(
        app,
        [
            "run",
            "--repo", str(FIXTURE),
            "--workspace", str(tmp_path / "ws"),
            "--checkpoint-db", str(tmp_path / "ckpt.sqlite"),
            "--store-url", f"sqlite:///{store}",
            "--no-sandbox",
        ],
    )
    # Recon ran deterministically (R1/R2 model calls fail gracefully with no
    # Ollama and are logged); the run stops at the next stub node, Hunt.
    assert r.exit_code == 3
    assert "stopped at stub node: hunt" in r.output
    assert "language=py" in r.output

    ws = tmp_path / "ws"
    assert (ws / ".git").is_dir()

    # Recon R0/R3 artifacts
    seed = json.loads((ws / "recon" / "seed.json").read_text())
    assert seed["primary_language"] == "python"
    assert (ws / "architecture.md").is_file()
    manifest = json.loads((ws / "recon" / "task_manifest.json").read_text())
    assert manifest["count"] >= 1
    classes = {c["attack_class"] for c in manifest["chunks"]}
    assert "unsafe_deserialization" in classes           # the pickle entry point
    assert not (classes & {"memory_oob_write", "use_after_free"})  # pruned for python

    # run row + checkpoint
    rows = list(sqlite3.connect(store).execute("select primary_language, status from runs"))
    assert rows == [("py", "running")]
    n = sqlite3.connect(tmp_path / "ckpt.sqlite").execute(
        "select count(*) from checkpoints"
    ).fetchone()[0]
    assert n >= 1
