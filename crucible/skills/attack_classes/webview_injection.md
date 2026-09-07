---
name: webview_injection
version: 0.1.0
description: Attacker content reaches a mobile WebView with script enabled or a JS bridge exposed, crossing into native.
languages: [java, kotlin, swift, objc, js]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `webview_injection` (§7).

## Attacker & boundary
Default attacker: a remote web origin, a MITM on cleartext content, or another
app supplying a URL/HTML the WebView loads. Boundary crossed: untrusted
web content → WebView JS context → (via a bridge) native capability.
Assumption broken: "only our first-party content runs here".

## Where to look
- `setJavaScriptEnabled(true)` + `addJavascriptInterface(obj, "name")` (pre-API
  17 or an interface exposing sensitive methods); `WKScriptMessageHandler`
- `loadUrl` / `loadDataWithBaseURL` with an attacker-influenced URL, `http://`,
  or a deep-link-supplied address
- `setAllowFileAccess`, `setAllowUniversalAccessFromFileURLs`,
  `setAllowFileAccessFromFileURLs` left enabled → `file://` reads app storage
- `shouldOverrideUrlLoading` allow-listing by `contains()` / prefix only
- disabled or ignored TLS errors in `onReceivedSslError`

## Move into execution (§9.2)
Instantiate the WebView config in a unit/instrumentation harness. Load a page
that calls the bridge method / a `file://` URL and assert the native side acts
or app-private data is returned. If a device/emulator is required,
`wishlist_write` with the exact need.

## PoC shape
`poc_test` FAILS clean (bridge invoked from untrusted origin / file read) and
PASSES patched (remove/gate the interface, `@JavascriptInterface` minimal
surface, disable file access, strict origin check). No source edits outside the
patch (§9.4).

## Output
`threat_model.boundary_crossed`: "web origin → JS bridge → native". Severity
`high`+ when the bridge reaches file/network/PII or intent dispatch.

## Anti-patterns to reject in your own output
- JS disabled and no bridge — not this class
- the loaded content is a bundled asset with a fixed `file:///android_asset`
  base and no user input
- attacker already has code execution on the device
