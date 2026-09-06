# skills/

Prompts are files with `version:` front-matter, recorded on every finding
(specs.md §4). They are the highest-value asset — diffable, versioned,
hot-swappable. Treat Recon prompt regressions as critical (§9.1).

```
skills/
  recon/
    subagent.md            # per-slice recon subagent
    merge.md               # (deterministic merger — no prompt, see nodes/recon.py)
  attack_classes/
    <name>.md              # front-matter (name, one-line desc) + methodology body
  validate/
    bug.md                 # Pass B — disprove-oriented
    reachability.md        # Pass C — attacker-input-to-sink
```

**Progressive disclosure (§7):** at Hunt start, load only the front-matter
(`name`, `description`) for every attack-class file; load the full methodology
body only for the scoped class. Loading the whole taxonomy upfront degrades
performance before work begins.

Phase 0 (the ~450-line single-session `security-audit` skill) is not built
here — this is the Phase 1 harness scaffold. Port the Phase 0 attacker
scenarios, bug classes, and anti-pattern detections into these files
near-unchanged when they exist (§2).
