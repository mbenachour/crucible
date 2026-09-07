---
name: unsafe_deserialization
version: 0.1.0
description: Attacker-controlled bytes are deserialized by a format that can instantiate arbitrary types or run callbacks.
languages: [py, js, go, ruby, php, java]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `unsafe_deserialization` (§7).

## Attacker & boundary
Default attacker: the producer of the serialized blob — network client, cookie
holder, queue publisher, file supplier, cache poisoner. Boundary crossed:
untrusted bytes → object graph / constructor / `__reduce__` / gadget chain.
Assumption broken: "this blob was written by us and is well-formed".

## Where to look
- `pickle.loads`, `yaml.load` (without `SafeLoader`), `marshal`, `shelve`,
  `jsonpickle`, `dill`; Node `node-serialize`, `funcster`; Ruby `Marshal.load`,
  `YAML.load`; Java `ObjectInputStream.readObject`; PHP `unserialize`
- signed/encrypted session or cookie blobs where the key is weak, absent, or the
  signature is checked *after* deserialization
- `__reduce__` / `__setstate__` / custom `Loader` / registered type hooks
- caches (Redis/memcache/disk) that store native-serialized values an attacker
  can influence upstream

## Move into execution (§9.2)
Build the smallest call that deserializes attacker bytes. Craft a payload whose
construction has an observable effect in `scratch/<task_id>/` (a class whose
`__reduce__` writes a marker file). For `yaml.load`, `!!python/object/apply`.
A crash from an unexpected type also demonstrates the missing type gate.

## PoC shape
`poc_test` FAILS clean (marker written / unexpected type instantiated) and
PASSES patched (safe loader, allow-listed types, signature-before-parse, or a
non-native format). No source edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "untrusted bytes → object construction".
`assumption_broken`: "blob is trusted / well-formed". Severity `critical` when
it reaches RCE pre-auth, `high` behind auth or for type-confusion DoS.

## Anti-patterns to reject in your own output
- the blob is HMAC-verified with a strong key *before* the parse call
- `json.loads` / a pure-data format with no type hooks — not this class
- attacker already runs code in-process (would just call the gadget directly)
