---
name: cert_pinning_bypass
version: 0.1.0
description: TLS trust is not (correctly) pinned/validated, so a network attacker can MITM the app's traffic.
languages: [java, kotlin, swift, objc, js]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `cert_pinning_bypass` (§7).

## Attacker & boundary
Default attacker: an active network attacker (hostile Wi-Fi, compromised
router, malicious VPN/proxy, a user-installed CA). Boundary crossed: "encrypted
authenticated channel to our server" → attacker-terminated TLS. Assumption
broken: "the peer presenting this cert is really our backend".

## Where to look
- `TrustManager` / `X509TrustManager` with empty `checkServerTrusted`;
  `HostnameVerifier` returning `true`; `setDefaultHostnameVerifier(ALLOW_ALL)`
- `URLSession` `didReceiveChallenge` calling
  `completionHandler(.useCredential, ...)` unconditionally;
  `NSAllowsArbitraryLoads` / ATS exceptions
- OkHttp with no `CertificatePinner`, or a pin set that is expired / for the
  wrong host / includes a debug CA
- `network_security_config.xml` trusting `user` CAs in release, or `<debug-
  overrides>` shipped
- Flutter/RN/Cordova HTTP layers with `badCertificateCallback => true`,
  `rejectUnauthorized: false`
- cleartext `http://` endpoints for sensitive traffic

## Move into execution (§9.2)
In a harness, feed the trust evaluation a cert chain signed by an untrusted CA
/ with a mismatched host and assert the connection is still accepted. Show the
verifier/pinner is absent or unconditionally permissive.

## PoC shape
`poc_test` FAILS clean (wrong-CA / wrong-host chain accepted) and PASSES
patched (system validation + correct pin set / hostname check, cleartext
disabled). No source edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "authenticated TLS → attacker MITM". Severity
`high` (credentials / session tokens exposed on the wire), `critical` if it
also enables response tampering into an RCE-adjacent sink.

## Anti-patterns to reject in your own output
- permissive trust is behind a `BuildConfig.DEBUG` / `#if DEBUG` guard not in
  the release artifact
- pinning is present and correct; the finding is "no backup pin"
- the endpoint carries only public, non-sensitive data
