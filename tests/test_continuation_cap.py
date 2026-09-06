"""Continuation cap enforcement (specs.md §8, §14.9)."""

from crucible.graph.hooks import MAX_CONTINUATIONS, continuation_gate


def _state(**over):
    base = dict(continuation_count=0, pending_hunts=[{"task_id": "x"}])
    base.update(over)
    return base  # type: ignore[return-value]


def test_gate_stops_at_hard_cap_even_with_work_pending():
    s = _state(continuation_count=MAX_CONTINUATIONS)
    assert continuation_gate(s) == "done"


def test_gate_continues_below_cap_with_work_pending():
    assert continuation_gate(_state(continuation_count=1)) == "continue"


def test_gate_done_when_no_work_pending():
    assert continuation_gate(_state(pending_hunts=[])) == "done"
