---
name: exported_component
version: 0.1.0
description: An Android/IPC component is reachable by other apps without the permission gate its actions require.
languages: [java, kotlin, swift, objc]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `exported_component` (§7).

## Attacker & boundary
Default attacker: any other installed app, with no special permission. Boundary
crossed: cross-app IPC surface → a privileged action or protected data inside
this app. Assumption broken: "only our own code / a permission holder invokes
this".

## Where to look
- `android:exported="true"` (or implicit-true via an `intent-filter`) on an
  `Activity` / `Service` / `BroadcastReceiver` / `ContentProvider` that
  performs sensitive work, with no `android:permission` / signature permission
- `ContentProvider` with `grantUriPermissions`, `openFile` building a path from
  the caller, SQL from `selection`/`selectionArgs` concatenated
- exported `Service` accepting commands via `Intent` action/extras
- `PendingIntent` handed out mutable / with a blank base intent (intent
  hijack / redirection)
- `sendBroadcast` of sensitive data with no receiver permission
- iOS: `NSExtension` / custom URL handlers / pasteboard used as IPC

## Move into execution (§9.2)
In a harness, deliver an `Intent` / `ContentResolver` call from a simulated
third-party caller and assert the privileged effect or data access happens.
For `ContentProvider` SQL, inject via `selection`. Device-only →
`wishlist_write`.

## PoC shape
`poc_test` FAILS clean (third-party invocation succeeds / data returned /
injection lands) and PASSES patched (`exported=false`, signature permission,
caller check, parameterized queries, immutable `PendingIntent`). No source
edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "third-party app IPC → privileged action".
Severity `high`+ for data theft / state change / SQLi via provider.

## Anti-patterns to reject in your own output
- component is `exported=false` and unreachable by filter
- it is exported but only does inert work (shows a static screen)
- a correct `signature`-level permission already gates it
