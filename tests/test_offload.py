"""Tool-output offloading threshold behavior (specs.md §7, §14.8)."""

from crucible.agents.instrumentation import ToolUsage
from crucible.agents.tools import ToolContext, offload_if_large


def _ctx(tmp_path):
    ws = tmp_path / "ws"
    (ws / "offload").mkdir(parents=True)
    return ToolContext(
        run_id="r", task_id="t", repo_path=str(tmp_path),
        workspace_path=str(ws), usage=ToolUsage("r", "hunter"),
    )


def test_small_output_passes_through(tmp_path):
    ctx = _ctx(tmp_path)
    out = "line\n" * 10
    assert offload_if_large(ctx, out) == out


def test_large_output_is_offloaded_to_disk(tmp_path):
    ctx = _ctx(tmp_path)
    out = "x" * 40_000  # ~10k tokens, well over threshold
    result = offload_if_large(ctx, out)
    assert "offloaded" in result and "full output:" in result
    assert len(result) < len(out)
    offload_files = list((tmp_path / "ws" / "offload").glob("*.txt"))
    assert len(offload_files) == 1
    assert offload_files[0].read_text() == out
