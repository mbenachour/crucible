---
name: memory_oob_write
version: 0.1.0
description: Out-of-bounds write — attacker-influenced index/length reaches a memory store past an allocation.
languages: [c, cpp]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `memory_oob_write` (§7).

## Attacker & boundary
Default attacker: unauthenticated producer of the external input that reaches
the allocation (network client, file, IPC peer). Boundary crossed: external
input → heap/stack write. State the assumption the code makes that the
attacker breaks (usually "length/index validated before use").

## Where to look
- `memcpy` / `memmove` / `strcpy` / array-index stores where the size or index
  derives from parsed input
- length fields read from the wire and used without an upper-bound check
- reallocation paths where the new size is computed with arithmetic that can
  wrap (pair with `integer_overflow`)
- off-by-one in loop bounds around fixed buffers

## Move into execution (§9.2)
Compile the smallest fragment that contains the write. Build a driver that
feeds a crafted input. Run under ASan/UBSan in the sandbox. A crash or an
ASan report is the PoC; "looks wrong on read" is not.

## PoC shape
`poc_test` must FAIL on the unmodified repo (ASan abort / assertion) and PASS
with `proposed_patch` applied. No source edits outside the patch (§9.4).

## Anti-patterns to reject in your own output
- tautological test (`assert True` after calling the parser) — deny-listed
- threat model where the attacker already has memory-write primitives
- exploit that runs but the input could never reach this code (that is Pass C's
  job to refute, but do not knowingly file it)
