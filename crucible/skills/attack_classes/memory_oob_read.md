---
name: memory_oob_read
version: 0.1.0
description: Out-of-bounds read — attacker-influenced index/length reaches a load past an allocation, leaking memory or crashing.
languages: [c, cpp]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `memory_oob_read` (§7).

## Attacker & boundary
Default attacker: unauthenticated producer of the external input that reaches
the load (network client, file, IPC peer). Boundary crossed: external input →
heap/stack read past a bound. State the assumption broken (usually
"length/offset validated against the actual buffer size before use").

## Where to look
- `memcpy` / `memmove` / `read` / array-index loads where size or index derives
  from parsed input, TLV / length-prefixed formats
- length taken from the wire, then used to bound a copy *out of* a smaller
  buffer (heartbleed shape)
- missing check that `offset + len <= buf_len`, or the check done in a wider
  type after a narrowing cast
- string functions on non-NUL-terminated input (`strlen`, `printf("%s")`,
  `atoi` walking off the end)
- off-by-one in loop bounds; `<=` where `<` was meant

## Move into execution (§9.2)
Compile the smallest fragment containing the load. Build a driver feeding a
crafted short buffer with an oversized length field. Run under
ASan/UBSan/Valgrind in the sandbox. An ASan `heap-buffer-overflow READ` /
`stack-buffer-overflow` report, or leaked adjacent bytes in the output, is the
PoC — "looks wrong on read" is not.

## PoC shape
`poc_test` FAILS on the unmodified repo (ASan abort / leaked bytes asserted)
and PASSES with `proposed_patch` (bounds check against the real size). No
source edits outside the patch (§9.4). If a build environment is missing,
`wishlist_write` with the exact toolchain need.

## Output
`threat_model.boundary_crossed`: "external input → OOB load". Severity `high`
for a remote info leak (keys, ASLR), `medium` for a read that only crashes.

## Anti-patterns to reject in your own output
- tautological test (`assert True` after calling the parser) — deny-listed
- threat model where the attacker already has a read primitive
- the bound check exists upstream in the only caller (verify it)
