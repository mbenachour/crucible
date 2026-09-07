# skills/

Prompts are files with `version:` front-matter, recorded on every finding
(specs.md §4). They are the highest-value asset — diffable, versioned,
hot-swappable. Treat Recon prompt regressions as critical (§9.1).

```
skills/
  recon/
    map.md                 # R1 — per-slice recon subagent
    threatmodel.md         # R2 — threat-model builder
    decompose.md           # R3 reference (deterministic — see recon/decompose.py)
  attack_classes/
    <name>.md              # front-matter (name, version, description, languages,
                           # builtin) + methodology body
  validate/
    bug.md                 # Pass B — disprove-oriented
    reachability.md        # Pass C — attacker-input-to-sink
```

**Progressive disclosure (§7):** at Hunt start, load only the front-matter
(`name`, `description`) for every attack-class file; load the full methodology
body only for the scoped class. Loading the whole taxonomy upfront degrades
performance before work begins. `crucible.skills.skill_front_matter` /
`load_skill` do exactly this split.

## Attack-class library (issue #16)

Every attack class the Recon decomposer (`crucible/recon/decompose.py`) can put
on the hunt queue has a methodology file. Body layout is uniform: *attacker &
boundary* / *where to look* / *move into execution (§9.2)* / *PoC shape* /
*output* / *anti-patterns to reject in your own output*.

| Group | Classes |
|---|---|
| Injection (web / CLI / lib) | `command_injection`, `sql_injection`, `template_injection`, `unsafe_deserialization`, `path_traversal`, `ssrf`, `xxe`, `argument_injection`, `dynamic_dispatch`, `injection_passthrough` |
| Access control | `auth_bypass` |
| Memory safety (C/C++) | `memory_oob_read`, `memory_oob_write`, `integer_overflow`, `use_after_free`, `format_string` |
| Parsing | `protocol_parsing` |
| Library correctness | `api_misuse`, `supply_chain` |
| Mobile | `webview_injection`, `insecure_storage`, `deeplink_handling`, `hardcoded_secret`, `cert_pinning_bypass`, `exported_component` |
| IaC / config | `misconfiguration`, `exposed_secret`, `excessive_permissions` |

`specialist` / `threat_fallback` chunks can carry a **model-invented**
`attack_class` (a `repo_specific_class` from Recon R2) with no file here — Hunt
falls back to the generic methodology and uses the chunk's `scope_hint` as the
per-repo playbook.

**Still owed:** tuning these against `fixture-c` / `fixture-py` until real
planted bugs surface (issue #3) — no golden fixtures are in-tree yet
(issue #30).

Phase 0 (the ~450-line single-session `security-audit` skill) is not built
here — this is the Phase 1 harness scaffold. Port the Phase 0 attacker
scenarios, bug classes, and anti-pattern detections into these files
near-unchanged when they exist (§2).
