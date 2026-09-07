---
name: template_injection
version: 0.1.0
description: Attacker-controlled string is rendered as template source, reaching code execution or data disclosure.
languages: [py, js, go, ruby, php, java]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `template_injection` (§7).

## Attacker & boundary
Default attacker: the lowest-privilege caller that controls a value later used
as *template text* (not template *data*). Boundary crossed: request field →
template compiler / expression evaluator. Assumption broken: "user input is
substituted into a fixed template, never compiled as one".

## Where to look
- `Template(user_value)`, `env.from_string(user_value)`, `render_template_string`,
  `Jinja2`/`Mako`/`Twig`/`ERB`/`Handlebars`/`Freemarker` fed a request field
- building the template path/name from input (`render(f"emails/{name}.html")`)
- format-string style: `str.format` / `%` on attacker input where the format
  spec itself is attacker-controlled (`"{0.__class__}"` reaches internals)
- server-side rendering of user "themes", "email templates", "report layouts"

## Move into execution (§9.2)
Render the smallest template call with a probe: `{{7*7}}` / `${7*7}` / `#{7*7}`
→ look for `49`. Escalate to attribute walking
(`{{''.__class__.__mro__}}` / `{{config}}`) and, where the engine allows it, a
call that touches `scratch/<task_id>/`. Prove evaluation, not just echo.

## PoC shape
`poc_test` FAILS clean (probe expression evaluated / object graph reached /
marker file written) and PASSES patched (input passed as context data, or an
autoescaping sandboxed environment). No source edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "<input> → template compile". Severity
`critical` when it reaches RCE / config, `high` for object-graph disclosure,
`medium` for reflected evaluation with no reachable sink.

## Anti-patterns to reject in your own output
- input rendered as *data* with autoescaping on — not template injection
- `{{7*7}}` echoed literally (`{{7*7}}`, not `49`) → no evaluation, no bug
- threat model where the "attacker" is the template author / an admin
