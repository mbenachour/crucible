---
name: recon_decompose
version: 0.1.0
description: R3 — reference for how the hunt queue is built. Not model-driven in Phase 1.
role: recon
---

# Reference (not a prompt)

R3 is **deterministic** in Phase 1 — see `crucible/recon/decompose.py`. This
file documents the chunk vocabulary so the mapping stays visible.

The hunt queue (`pending_hunts`) is a list of typed chunks:

| `chunk_type` | Built from | Meaning |
|---|---|---|
| `taint` | an entry point with a dynamic sink nearby in the same file | a concrete `source -> sink` path; "prove this is exploitable" |
| `catch_all` | entry point × compatible baseline attack class | sweep this entry point for this class |
| `risk` | a reflection / dynamic-dispatch fact | examine this dynamic call site |
| `specialist` | `repo_specific_classes` from R2 | apply this repo-specific methodology |
| `threat_fallback` | a STRIDE threat with no obvious code | go find where this threat lives |

Baseline attack classes come from the repo kind (`web_api` → OWASP-ish,
`native` → memory-safety, `mobile` → MASVS-ish, …) and are pruned against the
primary language (no memory classes in Python/JS).

If R3 ever becomes model-assisted, this file becomes its prompt.
