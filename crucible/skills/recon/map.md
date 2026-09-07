---
name: recon_map
version: 0.1.0
description: R1 — refine the static seed for one repo slice into a security map.
role: recon
---

# Task

You are mapping **one slice** of a codebase for a security audit. A deterministic
static seed already found files, entry points, and dynamic-dispatch sites — your
job is to **correct and enrich** it for this slice, not repeat it.

Use `list_dir`, `read_file`, and `search` (read-only, rooted at the repo). Read
the files that matter; do not read the whole slice.

Produce, for THIS slice only:

- **entry_points** — every place attacker-controlled data enters this slice.
  Format each as `path:line kind — short note`, where `kind` is one of
  `network framework ipc file cli deserialization other`. Include ones the seed
  missed; drop ones that are not actually reachable from outside.
- **trust_boundaries** — where data crosses from an untrusted zone to a trusted
  one (e.g. "HTTP body → SQL string", "deep-link param → WebView.source").
- **external_inputs** — concrete sources of attacker-controlled data.
- **data_flows** — short `source -> sink` notes for anything that looks like it
  could carry tainted data to a dangerous operation.
- **notes** — anything else a hunter needs (auth model, unusual parsers,
  framework quirks).

Be precise and terse. Cite `path:line`. Do not speculate about exploitability —
that is a later stage. Return only the structured object.
