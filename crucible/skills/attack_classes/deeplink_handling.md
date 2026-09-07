---
name: deeplink_handling
version: 0.1.0
description: A URI scheme / App Link / intent handler trusts its input, letting another app drive privileged in-app actions.
languages: [java, kotlin, swift, objc]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `deeplink_handling` (§7).

## Attacker & boundary
Default attacker: any other app on the device (or a web page) that can fire an
intent / open a custom-scheme or `https` App Link URL. Boundary crossed:
externally-supplied URI/intent extras → in-app navigation, state change, or a
downstream sink. Assumption broken: "this deep link was created by us / the
user's intent".

## Where to look
- `intent-filter` / `CFBundleURLSchemes` / `applinks:` entries → the handler
  that parses `getData()`, `queryParameter`, `intent.getExtras()`
- params flowing into: a WebView URL, `startActivity` of an implicit intent,
  auth-token acceptance, "confirm"/"pay"/"delete" flows, file paths, SQL
- `exported` activity/receiver with no permission; `android:autoVerify` missing
  (scheme hijack); iOS universal-link `apple-app-site-association` scope
- open redirect: `next=` / `return_url=` param followed without allow-listing
- account-linking / magic-link tokens accepted from a deep link without a
  nonce / user confirmation

## Move into execution (§9.2)
In a harness, deliver a crafted `Intent` / URL to the handler and assert the
privileged effect fires without user confirmation or a valid origin. For
WebView chaining, show the param reaches `loadUrl`.

## PoC shape
`poc_test` FAILS clean (privileged action / redirect / token acceptance from a
hostile link) and PASSES patched (origin & param allow-list, user
confirmation, nonce, non-exported or permission-gated component). No source
edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "external URI/intent → in-app action".
Severity `high` when it reaches auth/payment/PII/WebView, `medium` for
navigation-only.

## Anti-patterns to reject in your own output
- handler only routes to a static screen with no parameters used
- component is not exported and no `intent-filter` makes it reachable
- the link requires a secret only the legit server would know, and it is
  verified
