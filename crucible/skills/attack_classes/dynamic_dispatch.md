---
name: dynamic_dispatch
version: 0.1.0
description: Attacker-influenced name selects the code path — method, attribute, class, module, handler — reaching an unintended callable.
languages: [py, js, go, ruby, php, java]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `dynamic_dispatch` (§7). This
is the class the `risk` chunk type maps to — a reflection / dynamic-call site
the static seed flagged.

## Attacker & boundary
Default attacker: the lowest-privilege caller that influences the *selector* —
a string that becomes a method name, attribute, route target, class, task
name, event type, or import path. Boundary crossed: request field → symbol
resolution → call. Assumption broken: "the selector can only name one of a
small intended set".

## Where to look
- `getattr(obj, user)`, `setattr`, `obj[user](...)`, `globals()[user]`,
  `locals()`, `vars(obj)[user]`
- `importlib.import_module(user)`, `__import__`, `pydoc.locate`,
  `entry_points`, plugin loaders keyed by input
- dispatch tables / routers where the key is user input and the value space is
  wider than the intended handlers (`getattr(self, "handle_" + action)` with no
  prefix guarantee that `action` has no `_`, `.`, `__`)
- ORM / serializer field names, task-queue job names, webhook `event` → handler
- `eval` / `exec` where only an *identifier* is interpolated (still dangerous:
  reaches `__globals__`, `os`, dunder chains)

## Move into execution (§9.2)
Call the smallest dispatch site with a selector that resolves to something
outside the intended set — a private method, `__class__`, `os.system`, an
internal admin handler — and show an observable effect in
`scratch/<task_id>/`. If only a crash / AttributeError is reachable, that still
proves the selector is unconstrained.

## PoC shape
`poc_test` FAILS clean (unintended callable invoked / attribute reached) and
PASSES patched (explicit allow-list / dict of permitted names / `hasattr` on a
whitelist). No source edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "<input> → attribute/name resolution".
Severity scales with what the widest reachable callable does: `critical` for
RCE, `high` for privileged actions, `medium` for info / DoS.

## Anti-patterns to reject in your own output
- the selector is already constrained to a literal set / dict lookup with a
  default-deny
- the dynamic call exists but every reachable target is equivalently safe
- attacker already executes arbitrary code in the process
