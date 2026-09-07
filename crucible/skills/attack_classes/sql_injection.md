---
name: sql_injection
version: 0.1.0
description: Attacker-controlled string reaches a SQL query without parameterization.
languages: [py, js, go, ruby, php, java]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `sql_injection` (§7).

## Attacker & boundary
Default attacker: the lowest-privilege caller that can influence any field the
query reads (HTTP client, API consumer, queue message). Boundary crossed:
request/parameter → SQL grammar. Assumption broken: "this value is data, not
query syntax".

## Where to look
- string-formatted / concatenated SQL: f-strings, `%`, `+`, `.format`,
  template literals, `sprintf` feeding `execute` / `query` / `raw` / `exec`
- ORM escape hatches: `.raw()`, `.extra()`, `text()`, `QuerySet.annotate` with
  raw expressions, `session.execute("...")`
- identifiers that cannot be bound (table/column names, `ORDER BY`, `LIMIT`)
  built from input — parameterization does not cover these
- second-order: value stored now, concatenated into a query later
- `LIKE` clauses assembled by hand; JSON-path / array operators built from input

## Move into execution (§9.2)
Stand up the smallest slice that reaches the query (one handler + a SQLite or
the repo's test DB). Send `' OR '1'='1`, `';--`, a `UNION SELECT`, or a
sub-query that changes the row count. Prove the result set or side effect
changed — a boolean/error/timing oracle is enough.

## PoC shape
`poc_test` FAILS clean (extra rows returned, an injected row written, a
provoked SQL error) and PASSES with `proposed_patch` (bound parameters /
allow-listed identifier). No source edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "<input> → SQL string". `assumption_broken`:
name the field the code treated as inert. Severity `high` when the query runs
pre-auth or mutates data; `medium` for a read-only oracle behind auth.

## Anti-patterns to reject in your own output
- attacker already has direct DB access → tautology, deny-listed
- "uses string formatting" with a value that is a fixed enum / int-cast
- a test that asserts the query ran but injects nothing
