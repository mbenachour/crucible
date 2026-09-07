---
name: insecure_storage
version: 0.1.0
description: Sensitive data is written to a location another app or an attacker with device/backup access can read.
languages: [java, kotlin, swift, objc]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `insecure_storage` (§7).

## Attacker & boundary
Default attacker: a co-installed malicious app, someone with ADB / a device
backup, or a stolen unlocked/rooted device. Boundary crossed: in-memory secret
→ world/backup-readable persistent storage. Assumption broken: "only our app
can read this file".

## Where to look
- `SharedPreferences` / plist / SQLite / files holding tokens, passwords, PII,
  keys with `MODE_WORLD_READABLE`, on external storage, or unencrypted
- `EncryptedSharedPreferences` / Keychain / Keystore *not* used where they
  should be; keys stored next to the ciphertext
- `android:allowBackup="true"` for data that should not leave the device
- logging secrets (`Log.d`, `NSLog`, crash-reporter breadcrumbs)
- cache/temp files with secrets not cleared; WebView cache; clipboard
- iOS files without `NSFileProtectionComplete`; Keychain items with
  `kSecAttrAccessibleAlways`

## Move into execution (§9.2)
In a harness, trigger the write and inspect the resulting file: is it outside
the app sandbox, world-readable, or plaintext? Assert the secret bytes are
recoverable without the app's process. Device-only checks →
`wishlist_write`.

## PoC shape
`poc_test` FAILS clean (secret readable from the artifact) and PASSES patched
(Keystore/Keychain-backed encryption, internal storage, `allowBackup=false`,
file protection class). No source edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "app secret → externally-readable storage".
Severity tracks the secret (auth token → `high`, cached non-sensitive → `low`).

## Anti-patterns to reject in your own output
- data is already in Keystore/Keychain with sane accessibility
- the "secret" is non-sensitive (a UI flag, a public config)
- attacker model requires root AND the data is Keystore-encrypted
