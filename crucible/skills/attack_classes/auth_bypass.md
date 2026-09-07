---
name: auth_bypass
version: 0.1.0
description: A request reaches a protected capability without satisfying the intended authentication or authorization check.
languages: [py, js, go, ruby, php, java]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `auth_bypass` (§7).

## Attacker & boundary
Default attacker: an unauthenticated client, or an authenticated
low-privilege user reaching another user's data / an admin action. Boundary
crossed: public edge → protected capability. Assumption broken: name the check
that was supposed to run and did not (session valid, role sufficient, object
owned by caller, token unexpired).

## Where to look
- decorators / middleware that are order-sensitive, opt-in per route, or
  skipped for a method (`GET` guarded, `POST` not), a prefix, or `OPTIONS`
- IDOR: object id from the request used to fetch without an ownership filter
- JWT: `alg: none`, unverified signature, `kid` path/SQL injection, secret
  confusion (HS256 verified with a public key), missing `exp`/`aud`
- comparison bugs: `==` on secrets (timing), truthy checks on a parsed token,
  `if user.is_admin == "false"`, default-allow branches
- "internal" endpoints trusting a spoofable header (`X-Forwarded-For`,
  `X-Real-IP`, `X-User-Id`), debug/health routes exposing actions
- state machines: step N reachable without step N-1 (password reset, checkout)

## Move into execution (§9.2)
Drive the smallest slice with two identities (or none). Call the protected
route with the wrong / missing credential and assert it still acts. For IDOR,
authenticate as user A and fetch user B's object id. Show the guard is absent
or trivially satisfiable — not merely "looks weak".

## PoC shape
`poc_test` FAILS clean (protected action succeeds for the wrong principal) and
PASSES patched (guard added / applied to every method / ownership filter). No
source edits outside the patch (§9.4).

## Output
`threat_model.attacker`: be specific about the starting privilege.
`assumption_broken`: the exact check that failed to run. Severity `critical`
for unauthenticated admin/RCE-adjacent, `high` for cross-tenant data, `medium`
for low-value info.

## Anti-patterns to reject in your own output
- the "bypass" needs a credential the attacker would not have
- attacker is "authenticated admin" and the impact is an admin action →
  tautology, deny-listed
- a guard exists and is correct; the finding is "it could be stronger"
