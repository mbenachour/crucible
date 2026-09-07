---
name: hardcoded_secret
version: 0.1.0
description: A credential, key, or token is embedded in source/config/binary that an attacker can extract.
languages: [py, js, go, java, kotlin, swift, objc, ruby, php]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `hardcoded_secret` (§7).

## Attacker & boundary
Default attacker: anyone who can read the repo, a published package, a mobile
app bundle (trivially decompiled), a container image layer, or a JS bundle
served to browsers. Boundary crossed: "secret" → readable artifact.
Assumption broken: "this value is not shipped / not reachable".

## Where to look
- API keys, private keys, DB passwords, JWT/HMAC signing secrets, OAuth client
  secrets, cloud credentials, encryption keys as string literals or in
  committed `.env` / `config.*` / `strings.xml` / `Info.plist` / `*.pem`
- "default" or "example" credentials that are actually used if unset
- secrets in test fixtures that match production
- base64 / hex / ROT-obfuscated constants (still not secret)
- secrets in git history even if removed from HEAD
- client-side secrets that only work because the server does not enforce a
  second factor

## Move into execution (§9.2)
Grep the tree + build output. Show the secret is (a) real (matches a live
format / is referenced as a credential) and (b) reachable — present in the
shipped artifact (`pip wheel`, `./gradlew assembleRelease`, the JS bundle, the
image). If it grants access, note the capability; do not exercise it against a
live service (egress is blocked, §10).

## PoC shape
`poc_test` FAILS clean (asserts the secret string is present in the built
artifact / importable module) and PASSES patched (value read from env /
secret manager; placeholder in source). No source edits outside the patch
(§9.4). Rotating the real secret is a `wishlist_write` item.

## Output
`threat_model.boundary_crossed`: "credential → shipped artifact". Severity
`critical` for a live production credential with broad scope, `high` for
scoped, `low` for a dev/test-only value with no prod reach.

## Anti-patterns to reject in your own output
- the value is a public identifier (client_id, publishable key) — not secret
- it is a placeholder that is overridden at deploy and fails closed if not
- a secret already loaded from env / vault; the finding is "it *could* be
  hardcoded"
