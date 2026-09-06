---
name: validate_reachability
version: 0.1.0
description: Pass C — can attacker-controlled input actually reach this code from outside the system?
role: validator_reach
---

# Task

Separate agent, separate call from Pass B (specs.md §1.7). Assume the defect
is real. **Determine whether an external attacker's input can reach it.**

This is the stage that converts "there is a flaw" into "there is a reachable
vulnerability" — the finding that matters.

## Procedure
1. Start from the finding's `threat_model.attacker` and the entry points in
   `architecture.md`.
2. Trace forward: is there a call path from an external input (HTTP route, CLI,
   file parser, IPC) to `file_path:line_start`? Name each hop.
3. Check guards on that path: auth, feature flags, config that is off by
   default, input that is sanitized before the sink.
4. Phase 1: single repo only. If the path leaves this repo, record
   `verdict: refuted` with `reasoning` noting it needs the Phase 3 cross-repo
   Tracer.

## Output (strict JSON, guided generation)
```json
{ "verdict": "upheld" | "refuted", "reasoning": "<the call path, hop by hop, or where it breaks>" }
```
