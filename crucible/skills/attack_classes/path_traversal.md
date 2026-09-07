---
name: path_traversal
version: 0.1.0
description: Attacker-controlled path component escapes an intended directory, reaching arbitrary file read or write.
languages: [py, js, go, ruby, php, java]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `path_traversal` (§7).

## Attacker & boundary
Default attacker: the lowest-privilege caller that supplies a filename, key,
path segment, or archive entry. Boundary crossed: request field → filesystem
path resolution. Assumption broken: "this segment is a bare name inside our
directory".

## Where to look
- `open(base + name)`, `os.path.join(root, user)` with no post-join containment
  check, `send_file` / `sendfile` / static handlers, `Path(root) / user`
- `../`, absolute paths, `..%2f`, UTF-8 / double URL encoding, NUL bytes,
  Windows `\` and drive letters, leading `/`
- archive extraction ("zip slip"): entry names from a user-supplied tar/zip
- symlink following; `os.path.join` discarding `root` when `user` is absolute
- key-value / cache / upload stores that map a user key straight to a path

## Move into execution (§9.2)
Call the smallest resolver with `../../../../etc/hostname` (read) or a write
target outside the intended dir. Confirm containment is broken by reading a
file the caller should not reach, or writing into `scratch/<task_id>/../`.
Compute the resolved realpath and assert it is (not) under the base.

## PoC shape
`poc_test` FAILS clean (out-of-tree file read / written) and PASSES patched
(`os.path.realpath` prefix check, `Path.resolve().is_relative_to(base)`, or an
allow-list). No source edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "<input> → path resolution". Severity `high`
for arbitrary read of sensitive files or arbitrary write; `medium` for reads
confined to a low-value tree.

## Anti-patterns to reject in your own output
- a containment check on the resolved realpath is already present and correct
- input is an integer id / allow-listed enum mapped to a fixed path
- attacker already has shell / filesystem access on the host
