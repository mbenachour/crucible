---
name: command_injection
version: 0.1.0
description: Attacker-controlled string reaches a shell or process-spawn without neutralization.
languages: [py, js, go, ruby, php]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `command_injection` (§7).

## Attacker & boundary
Default attacker: the lowest-privilege caller that can influence the argument
(HTTP client, queue message, CLI flag from an untrusted wrapper). Boundary:
request/parameter → shell interpretation. Assumption broken: "input is a bare
filename/identifier, not shell syntax".

## Where to look
- `os.system`, `subprocess.*(..., shell=True)`, `Popen(str)`, backticks,
  `child_process.exec`
- string-formatted commands built from request fields, filenames, env vars
- "safe" wrappers that still pass through a shell for globbing/pipes
- second-order: value stored now, concatenated into a command later

## Move into execution (§9.2)
Stand up the smallest runnable slice (a single view/handler). Send an input
containing `; touch $SCRATCH/pwned` (or `$(...)`). Confirm the side effect
inside `scratch/<task_id>/`. Network egress is denied by default (§10) — prove
it with a local filesystem effect, not a callback.

## PoC shape
`poc_test` FAILS clean (the marker file appears / unexpected exit) and PASSES
patched. No source edits outside `proposed_patch` (§9.4).

## Anti-patterns to reject in your own output
- attacker already has local shell access → tautology, deny-listed
- test that asserts `exec` executed something without an injected effect
- injection into a command that runs with no privilege boundary crossed
