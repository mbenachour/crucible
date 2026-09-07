---
name: api_misuse
version: 0.1.0
description: A library's own use of a security-sensitive API is wrong, weakening every consumer (crypto, randomness, TLS, comparisons).
languages: [py, js, go, ruby, php, java]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `api_misuse` (§7). Library
repos: the flaw is in *how the library calls a primitive*, and it ships to
every consumer.

## Attacker & boundary
Default attacker: a network attacker or a co-located user against an
application built on this library. Boundary crossed: the guarantee the
library's API advertises (confidentiality, integrity, unpredictability,
authentication) → the weaker property it actually provides. Assumption broken:
name the promised property.

## Where to look
- crypto: ECB mode, static/zero IV or nonce, `MD5`/`SHA1` for integrity,
  home-grown constructions, key derived by a single hash, PKCS#1 v1.5,
  unauthenticated encryption
- randomness: `random`/`Math.random`/`rand()` for tokens, session ids, salts,
  password-reset codes, filenames that must be unpredictable
- comparisons: `==` / `strcmp` on MACs, tokens, signatures (timing) instead of
  a constant-time compare
- TLS: `verify=False`, disabled hostname check, custom `TrustManager`
  accepting all, protocol downgrade
- JWT/paseto/session helpers that skip `exp`/`aud`/`iss`, accept `alg: none`
- predictable IDs (sequential, timestamp-based) where capability rests on
  unguessability

## Move into execution (§9.2)
Write the smallest harness exercising the library's API and *measure the
property*: two encryptions of the same block are identical (ECB); N generated
tokens have < expected entropy / are reproducible from a seed the attacker can
know; a tampered MAC still verifies; a wrong-host cert is accepted. Numbers,
not adjectives.

## PoC shape
`poc_test` FAILS clean (the measurement shows the weak property) and PASSES
patched (authenticated mode / CSPRNG / constant-time compare / verification
on). No source edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "advertised <property> → actual <weaker
property>". Severity `high` for broken auth/confidentiality primitives,
`medium` for defense-in-depth gaps.

## Anti-patterns to reject in your own output
- `MD5`/`random` used somewhere non-security-sensitive (a cache key, a test)
- "should use a KDF" with no reachable attacker who benefits
- the weak call is on a documented "insecure, do not use in prod" helper
