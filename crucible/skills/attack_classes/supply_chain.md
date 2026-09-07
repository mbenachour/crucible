---
name: supply_chain
version: 0.1.0
description: The build/dependency/release path lets an attacker inject code that ships to consumers.
languages: [py, js, go, ruby, php, java]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `supply_chain` (§7). Static,
mostly: the "sandbox" here is a dry-run of the build/install path.

## Attacker & boundary
Default attacker: whoever can influence a dependency source, a build input, or
a CI step — a typosquat author, a compromised transitive maintainer, an
attacker who can open a PR, a network MITM on an unpinned fetch. Boundary
crossed: external artifact / build input → code that runs at install, build,
test, or import time in a consumer or in CI. Assumption broken: "everything we
build from is what we think it is".

## Where to look
- `setup.py` / `conftest.py` / `__init__.py` / npm `preinstall`/`postinstall`
  running network or shell at install
- unpinned deps (ranges, no lockfile), lockfile not enforced, `--pre`,
  `latest`, git URLs on a branch, `pip install` from an extra index without
  scoping
- fetches over `http://`, no checksum / signature / hash pin on downloaded
  binaries, blobs, or install scripts
- CI: `pull_request_target` with checkout of PR code + secrets, unpinned
  third-party actions (`@main`), cache poisoning, self-hosted runner exposure
- dependency confusion: an internal package name also resolvable on a public
  index
- vendored code with no provenance / a build step that regenerates from a
  remote source

## Move into execution (§9.2)
Dry-run the path in the sandbox: does `pip install .` / `npm ci` /
the CI workflow execute attacker-influenceable code before any review gate?
Show the missing pin/checksum by pointing the fetch at a local file and
observing it is accepted without verification. No egress needed.

## PoC shape
`poc_test` FAILS clean (install/build runs unpinned/unverified code, or an
unexpected artifact is accepted) and PASSES patched (hash-pinned lockfile,
checksum/signature check, action SHA-pin, `preinstall` removed). No source
edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "build input → code execution in
consumer/CI". Severity `critical` for unauthenticated code exec in the release
artifact, `high` for CI secret exfil, `medium` for an unpinned-but-reputable
dep.

## Anti-patterns to reject in your own output
- "a dependency *could* be compromised" with no concrete missing control
- a dev-only tool dependency with no path into the shipped artifact
- pins and checksums are present and enforced; the finding is stylistic
