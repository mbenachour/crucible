---
name: integer_overflow
version: 0.1.0
description: Attacker-influenced arithmetic wraps or truncates, defeating a size/bounds check and enabling a memory bug.
languages: [c, cpp]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `integer_overflow` (§7).
Usually a primitive that chains into `memory_oob_write` / `memory_oob_read` —
report the reachable memory effect, not the arithmetic alone.

## Attacker & boundary
Default attacker: unauthenticated producer of the external input that feeds the
computation (a count, length, element size, index). Boundary crossed: attacker
integer → wrapped/truncated value → undersized allocation or bypassed check →
memory corruption. Assumption broken: "this size arithmetic cannot overflow".

## Where to look
- `malloc(count * size)` / `alloca(n)` / `realloc(p, a + b)` with operands from
  input
- `len + header`, `n * elemsize`, `1 << shift` where the result feeds a bound
- signed/unsigned confusion: negative length read as huge `size_t`; `int` count
  compared `< max` then used as `size_t`
- narrowing casts: `size_t` → `int` → back; `uint32` length stored in `uint16`
- `if (a + b > limit)` where `a + b` wraps below `limit`

## Move into execution (§9.2)
Compile the smallest fragment doing the arithmetic and the subsequent
alloc/copy. Feed operands that wrap (e.g. `0xFFFFFFFF`, `SIZE_MAX/2 + 1`,
negative). Run under ASan + UBSan (`-fsanitize=undefined,address`) in the
sandbox: a UBSan overflow report *plus* an ASan overflow at the downstream
copy is the PoC.

## PoC shape
`poc_test` FAILS on the unmodified repo (sanitizer abort / undersized buffer
written past) and PASSES with `proposed_patch` (checked arithmetic /
`__builtin_mul_overflow` / width-correct types). No source edits outside the
patch (§9.4).

## Output
`threat_model.boundary_crossed`: "attacker integer → size check bypass → memory
write". Severity tracks the downstream corruption (usually `high`).

## Anti-patterns to reject in your own output
- overflow with no reachable memory / logic consequence
- operands are bounded by an earlier validated check (verify the range)
- attacker already controls the allocation size directly without wrapping
