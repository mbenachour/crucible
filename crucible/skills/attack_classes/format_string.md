---
name: format_string
version: 0.1.0
description: Attacker-controlled data is used as a format string, reaching memory read/write or a crash.
languages: [c, cpp]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `format_string` (§7).

## Attacker & boundary
Default attacker: the supplier of the string that reaches the format argument —
a request field, a filename, an env var, a log line, a config value. Boundary
crossed: attacker text → `printf`-family format parser → stack/`%n` writes,
stack disclosure. Assumption broken: "this string is only ever printed, never
interpreted".

## Where to look
- `printf(user)`, `fprintf(f, user)`, `sprintf(buf, user)`, `snprintf(buf, n,
  user)`, `syslog(pri, user)`, `err`/`warn`, `vasprintf`, `g_string_printf`
- logging wrappers that pass a caller-formatted message straight to `*printf`
- `dgettext`/translated format strings where the catalog is attacker-influenced
- `%n` reachable (not disabled by `_FORTIFY_SOURCE` / `-Wformat-security`)

## Move into execution (§9.2)
Compile the smallest call. Feed `%x %x %x %s` and `%n` variants. Under ASan and
`-D_FORTIFY_SOURCE=2 -Wformat -Wformat-security` in the sandbox: a crash, an
`%n`-write abort, or stack bytes appearing in the output is the PoC.

## PoC shape
`poc_test` FAILS on the unmodified repo (crash / disclosed stack bytes) and
PASSES with `proposed_patch` (`printf("%s", user)` / a constant format). No
source edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "attacker text → format parser". Severity
`high` (write/disclosure); `medium` if only a crash is reachable.

## Anti-patterns to reject in your own output
- the format argument is a string literal; user data is a `%s` operand — safe
- compiler already rejects it (`-Werror=format-security`) so it cannot ship
- attacker already has code execution
