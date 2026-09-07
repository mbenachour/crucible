"""CLI smoke tests — everything up to the (still-stubbed) pipeline nodes."""

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


def test_run_reaches_stub_node_and_persists_run_row(tmp_path):
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
    # Substrate ran; graph dispatched; first stub node raised.
    assert r.exit_code == 3
    assert "stopped at stub node: recon" in r.output
    assert "language=py" in r.output

    # workspace initialised
    ws = tmp_path / "ws"
    assert (ws / ".git").is_dir()
    for sub in ("coverage", "findings", "offload", "scratch"):
        assert (ws / sub).is_dir()

    # run row written with detected language, status 'running'
    rows = list(
        sqlite3.connect(store).execute("select primary_language, status from runs")
    )
    assert rows == [("py", "running")]

    # checkpointer wrote state (resume is possible)
    n = sqlite3.connect(tmp_path / "ckpt.sqlite").execute(
        "select count(*) from checkpoints"
    ).fetchone()[0]
    assert n >= 1
