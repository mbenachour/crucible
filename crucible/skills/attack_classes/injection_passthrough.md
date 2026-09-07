---
name: injection_passthrough
version: 0.1.0
description: A library forwards caller input into a dangerous sink without neutralizing it, and its API implies it is safe.
languages: [py, js, go, ruby, php, java]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `injection_passthrough` (§7).
This is the library-repo analogue of the injection classes: the vulnerable
party is a *downstream consumer*, and the bug is the library's contract.

## Attacker & boundary
Default attacker: an attacker against an application that uses this library as
documented. Boundary crossed: the library's public API → a shell / SQL /
path / template / eval sink inside the library, with input the caller
reasonably believed was treated as data. Assumption broken: "the library
escapes / parameterizes / validates what I hand it".

## Where to look
- helper functions named `run`, `exec_query`, `render`, `fetch`, `load`,
  `safe_*` that concatenate a parameter into a sink
- format/URL/command builders exported for convenience
- default arguments that are unsafe (`shell=True`, `autoescape=False`,
  `verify=False`, `allow_pickle=True`) where the docs do not flag it
- callback/hook registration that `eval`s or imports a name
- "sanitizer" utilities that are incomplete (blocklist, single-pass replace,
  no encoding-awareness)

## Move into execution (§9.2)
Call the library's public function exactly as its docstring/README shows, with
an input a normal caller would treat as inert, and reach the internal sink
with an effect in `scratch/<task_id>/`. The PoC must go through the public
API, not a private helper.

## PoC shape
`poc_test` FAILS clean (public call produces the injected effect) and PASSES
patched (the library escapes/parameterizes, or flips the unsafe default and
documents it). No source edits outside the patch (§9.4).

## Output
`threat_model.attacker`: "attacker against a consumer using the documented
API". `boundary_crossed`: "library API → <internal sink>". Severity reflects
the sink and whether the unsafe behaviour is the default.

## Anti-patterns to reject in your own output
- the docs explicitly say "caller must pre-escape" and the name does not imply
  safety
- the only way to trigger it is to pass an obviously-shell string to a function
  named `shell_command`
- reclassify to the specific injection class if the repo is an application, not
  a library
