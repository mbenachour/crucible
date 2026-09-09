"""Report stage (specs.md §9.5).

Deterministic — no model call. Selects the findings that survived **both**
model passes (`reach_upheld`), writes queryable `report.json` plus a
human-readable `report.md`, and records run metrics (funnel counts, fork rate,
per-tool invocation counts). Full provenance per finding: hunter model + prompt
version + sampling params, and the verdict trail from every validation pass.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from crucible.graph.state import CrucibleState
from crucible.obs import span
from crucible.workspace.fs import commit_node

log = logging.getLogger("crucible.report")

_UPHELD_STATUS = "reach_upheld"


def run(state: CrucibleState, deps=None) -> CrucibleState:
    ws = Path(state["workspace_path"])
    run_id = state["run_id"]
    store = getattr(deps, "store", None)

    rows = store.run_findings(run_id) if store is not None else []
    by_status = Counter(r.status for r in rows)
    upheld = [r for r in rows if r.status == _UPHELD_STATUS]

    with span("report", run_id=run_id, upheld=len(upheld)):
        metrics = _metrics(store, run_id, state)
        report = {
            "run_id": run_id,
            "repo": state["repo_path"],
            "repo_commit": state["repo_commit"],
            "language": state.get("primary_language", ""),
            "generated_at": datetime.now(UTC).isoformat(),
            "recon_quality": state.get("recon_quality", ""),
            "counts": {
                "total": len(rows),
                "upheld": len(upheld),
                **{s: c for s, c in sorted(by_status.items())},
            },
            "metrics": metrics,
            "findings": [_render_finding(store, r) for r in _sorted(upheld)],
        }
        (ws / "report.json").write_text(json.dumps(report, indent=2, default=str))
        (ws / "report.md").write_text(_markdown(report))

    state["report_path"] = str(ws / "report.json")
    commit_node(ws, "report", run_id)
    log.info(
        "report  %d upheld / %d findings  (%s)  -> report.json",
        len(upheld), len(rows),
        ", ".join(f"{s}={c}" for s, c in sorted(by_status.items())) or "empty",
    )
    return state


# --------------------------------------------------------------------- internals

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _sorted(rows):
    return sorted(
        rows,
        key=lambda r: (_SEVERITY_ORDER.get((r.payload or {}).get("severity", ""), 4), r.finding_id),
    )


def _metrics(store, run_id: str, state: CrucibleState) -> dict:
    usage = store.tool_usage(run_id) if store is not None else []
    by_tool = {}
    for u in usage:
        t = by_tool.setdefault(u.tool_name, {"count": 0, "errors": 0})
        t["count"] += u.count
        t["errors"] += u.errors
    forks = sum(u.count for u in usage if u.tool_name == "fork_sibling")
    hunt_exec = sum(u.count for u in usage
                    if u.role == "hunter" and u.tool_name in ("bash", "sandbox_exec"))
    return {
        "cycles": state.get("cycle_count", 0),
        "continuations": state.get("continuation_count", 0),
        "fork_count": state.get("fork_count", 0),
        "token_spend": state.get("token_spend", 0),
        "fork_rate": f"{forks}/{hunt_exec}" if hunt_exec else f"{forks}/0",
        "tool_usage": by_tool,
    }


def _render_finding(store, row) -> dict:
    p = dict(row.payload or {})
    trail = []
    if store is not None:
        for pass_name in ("mechanical", "bug", "reachability"):
            for reason in store.finding_reasons(row.finding_id, pass_name):
                trail.append({"pass": pass_name, "reasoning": reason})
    return {
        "finding_id": row.finding_id,
        "severity": p.get("severity", ""),
        "title": p.get("title", ""),
        "file_path": p.get("file_path", ""),
        "line_start": p.get("line_start"),
        "line_end": p.get("line_end"),
        "threat_model": p.get("threat_model", {}),
        "description": p.get("description", ""),
        "poc_test": p.get("poc_test", ""),
        "proposed_patch": p.get("proposed_patch", ""),
        "provenance": {
            "hunter_model": getattr(row, "hunter_model", ""),
            "hunter_prompt_version": getattr(row, "hunter_prompt_version", ""),
            "hunter_sampling": getattr(row, "hunter_sampling", {}),
        },
        "validation_trail": trail,
    }


def _markdown(report: dict) -> str:
    L: list[str] = [
        f"# Security report — {report['repo']}",
        "",
        (f"- run: `{report['run_id']}`  commit: `{report['repo_commit'][:12]}`  "
         f"language: {report['language']}  recon: {report['recon_quality'] or 'n/a'}"),
        f"- generated: {report['generated_at']}",
        f"- funnel: {', '.join(f'{k}={v}' for k, v in report['counts'].items())}",
        (f"- metrics: cycles={report['metrics']['cycles']} "
         f"forks={report['metrics']['fork_rate']} tokens={report['metrics']['token_spend']}"),
        "",
        f"## Upheld findings ({report['counts']['upheld']})",
        "",
    ]
    if not report["findings"]:
        L.append("_None survived both the bug and reachability passes._")
        return "\n".join(L) + "\n"
    for f in report["findings"]:
        tm = f["threat_model"] or {}
        L += [
            f"### [{f['severity'].upper()}] {f['title']}  · `{f['finding_id']}`",
            f"`{f['file_path']}:{f['line_start']}-{f['line_end']}`",
            "",
            f"- **attacker:** {tm.get('attacker', '')}",
            f"- **boundary crossed:** {tm.get('boundary_crossed', '')}",
            f"- **assumption broken:** {tm.get('assumption_broken', '')}",
            "",
            f["description"].strip(),
            "",
            "<details><summary>PoC test</summary>",
            "",
            "```",
            f["poc_test"].strip(),
            "```",
            "</details>",
            "",
            "<details><summary>Proposed patch</summary>",
            "",
            "```diff",
            f["proposed_patch"].strip(),
            "```",
            "</details>",
            "",
        ]
        if f["validation_trail"]:
            L.append("Validation trail:")
            L += [f"- _{t['pass']}_: {t['reasoning']}" for t in f["validation_trail"]]
            L.append("")
    return "\n".join(L) + "\n"
