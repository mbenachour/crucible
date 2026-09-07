---
name: use_after_free
version: 0.1.0
description: A pointer is used after its allocation is freed (or freed twice), giving an attacker control of freed memory.
languages: [c, cpp]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `use_after_free` (§7).

## Attacker & boundary
Default attacker: the party that drives the object's lifecycle through external
input — request sequencing, connection teardown, parse errors, callbacks,
reference counting. Boundary crossed: attacker-controlled control flow →
dangling-pointer dereference / double free → freed-chunk contents under
attacker influence. Assumption broken: "this object outlives every use" or
"this cleanup path runs exactly once".

## Where to look
- error/cleanup paths that `free` then fall through to code still using the
  pointer; `goto fail` ladders
- freeing a struct while a child holds a back-pointer; callback that frees its
  own context
- refcount bugs: decrement without owning a reference, missing increment before
  handing out a pointer, races on `close`/`destroy`
- realloc invalidating an alias kept elsewhere
- container element freed while an iterator / cached index still points at it
- double free on repeated `close()` / re-entrant signal handlers

## Move into execution (§9.2)
Compile the smallest fragment reproducing the lifecycle. Drive the sequence
(open → error → use, or free → free) from a test. Run under ASan in the
sandbox: `heap-use-after-free` or `double-free` report is the PoC. Where ASan
is unavailable, a deterministic crash under a poisoned allocator.

## PoC shape
`poc_test` FAILS on the unmodified repo (ASan UAF/double-free) and PASSES with
`proposed_patch` (null after free, reorder cleanup, fix refcount). No source
edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "attacker-sequenced lifecycle → dangling
dereference". Severity `high` (often `critical` if a freed vtable/function
pointer is reachable).

## Anti-patterns to reject in your own output
- the "use" after free is unreachable / dead code
- single-threaded, single-caller path where the free provably follows every use
- attacker already has an arbitrary-write primitive
