---
name: argument_injection
version: 0.1.0
description: Attacker-controlled data becomes extra CLI arguments/options to a spawned program (no shell needed).
languages: [py, js, go, ruby, php, java]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `argument_injection` (§7).

## Attacker & boundary
Default attacker: the lowest-privilege caller that influences a value passed as
an argv element to a subprocess — even with `shell=False`. Boundary crossed:
request/parameter → the *option parser* of the invoked tool. Assumption
broken: "this value is an operand (a path, a ref), not a flag".

## Where to look
- `subprocess.run(["git", "clone", user_url, dest])` where `user_url` can start
  with `-` (`--upload-pack=...`, `-o ProxyCommand=...`)
- wrappers around `git`, `ssh`, `curl`, `tar`, `rsync`, `ffmpeg`, `find`,
  `hg`, `mysql`, `psql`, `zip`, `openssl` fed user strings as positional args
- missing `--` end-of-options separator before user-controlled operands
- values that look safe (a branch name, a filename) but reach a tool that
  treats leading `-` as an option

## Move into execution (§9.2)
Invoke the smallest wrapper with a value beginning `-` that changes tool
behaviour with a filesystem-visible effect in `scratch/<task_id>/` (e.g.
`--output`, `-o`, `--upload-pack` pointing at a marker script, `tar
--to-command`). Prove the injected flag took effect; egress stays blocked
(§10), so use a local effect.

## PoC shape
`poc_test` FAILS clean (injected option changed behaviour / wrote a marker) and
PASSES patched (`--` separator, reject leading `-`, allow-list, or a native
API instead of the CLI). No source edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "<input> → argv option parsing". Severity
`high` when a flag reaches code exec / arbitrary file write, `medium` for
behaviour tampering.

## Anti-patterns to reject in your own output
- `shell=True` string concatenation → that is `command_injection`, file it there
- the value is already validated to a strict pattern with no leading `-`
- attacker already controls the argv of the parent process
