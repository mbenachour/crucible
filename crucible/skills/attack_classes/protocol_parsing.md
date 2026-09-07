---
name: protocol_parsing
version: 0.1.0
description: A parser for a wire/file format mishandles malformed or hostile input, reaching memory corruption, DoS, or state confusion.
languages: [c, cpp, py, js, go, rust, java]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `protocol_parsing` (§7).
Broad class: the target is a decoder for a network protocol, a binary/text file
format, a serialization framing, or a header set.

## Attacker & boundary
Default attacker: an unauthenticated peer or file supplier who fully controls
the bytes and their framing. Boundary crossed: hostile input → parser state /
memory / downstream consumer. Assumption broken: name the invariant the parser
relies on but does not enforce (length ≤ remaining, field count bounded,
chunked size honest, no negative offsets, one interpretation of the framing).

## Where to look
- length / count / offset fields used before validation against remaining input
- state machines that don't reset on error; partial-parse results consumed on
  the failure path
- recursion / nesting with no depth limit (stack exhaustion), quadratic work on
  small input (decompression bombs, `O(n^2)` reassembly)
- integer issues in size math (pair with `integer_overflow`)
- **parser differential**: two components parse the same bytes differently
  (request smuggling, MIME/encoding confusion, `Content-Length` vs
  `Transfer-Encoding`, path/host normalization mismatch)
- signed/unsigned, endianness, alignment, off-by-one in TLV walking

## Move into execution (§9.2)
Build the smallest driver that calls the parser on raw bytes. Fuzz-lite: hand
it truncated frames, oversized lengths, deep nesting, and two-interpretation
inputs. C/C++: ASan/UBSan + a libFuzzer or afl harness in the sandbox — a crash
or hang is the PoC. Managed languages: an unhandled exception on the hot path,
a wall-clock blowup against the sandbox ceiling, or a demonstrated differential.

## PoC shape
`poc_test` FAILS on the unmodified repo (sanitizer abort / timeout / mismatched
interpretation asserted) and PASSES with `proposed_patch` (bound check, depth
limit, strict framing, single canonical parse). No source edits outside the
patch (§9.4).

## Output
`threat_model.boundary_crossed`: "hostile <format> bytes → parser
<memory|state>". Severity `high` for memory corruption / smuggling, `medium`
for parser-only DoS.

## Anti-patterns to reject in your own output
- input is produced by a trusted peer over an authenticated channel only
- "the parser is complex" with no concrete malformed input that breaks it
- a crash on input that could never reach this parser (Pass C's job, but do not
  knowingly file it)
