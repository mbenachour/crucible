---
name: ssrf
version: 0.1.0
description: Attacker controls the destination of a server-side request, reaching internal services or cloud metadata.
languages: [py, js, go, ruby, php, java]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `ssrf` (§7).

## Attacker & boundary
Default attacker: the lowest-privilege caller that supplies a URL, host, port,
or anything the server later fetches (webhook target, avatar URL, "import from
link", PDF/image renderer, OpenID/OAuth discovery URL). Boundary crossed:
request field → outbound socket from a trusted network position. Assumption
broken: "this URL points somewhere external and harmless".

## Where to look
- `requests.get(user_url)`, `urllib`, `httpx`, `fetch`, `curl`, `Net::HTTP`,
  headless browsers, image/PDF/thumbnail pipelines, XML/`<img>`/SVG loaders
- redirect following that re-resolves to an internal host after an external one
- DNS rebinding surface: host validated once, resolved again at connect time
- URL parsers that disagree (`http://expected@169.254.169.254/`, `#`, `\`,
  octal/hex IPs, `[::1]`, `0.0.0.0`, trailing dot)
- protocol smuggling: `file://`, `gopher://`, `dict://`, `ftp://`

## Move into execution (§9.2)
Network egress is denied in the sandbox (§10) — do **not** rely on a callback.
Instead prove the *destination selection* is attacker-controlled and
unvalidated: assert the code would connect to `169.254.169.254` /
`127.0.0.1:<internal>` / a `file://` URL by intercepting the client
(monkeypatch the transport, capture the resolved address) and showing no
allow-list / no post-resolution check rejects it.

## PoC shape
`poc_test` FAILS clean (transport invoked with an internal/again-resolved
address, or a non-HTTP scheme accepted) and PASSES patched (scheme allow-list
+ resolve-then-check against private ranges + pinned connect). No source edits
outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "<input> → server-side outbound request".
`assumption_broken`: "destination is external / validated". Severity `high`
when metadata or an unauthenticated internal service is reachable, `medium`
for blind SSRF with no known sink.

## Anti-patterns to reject in your own output
- destination is a fixed constant / signed by us / from a strict allow-list
- "user controls a query param on our own fixed API host" — no boundary crossed
- claiming SSRF from a callback that only fails because egress is blocked
