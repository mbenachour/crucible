"""R3 — decompose the seed + threat model into a typed Hunt queue (issue #5).

Deterministic (specs.md §1.8). `threat_model` may be `None` (R2 skipped or
failed) — decompose still works off the seed alone.

Also renders `architecture.md` from the seed + R1 contributions.
"""

from __future__ import annotations

import re

from crucible.recon.schema import (
    AttackSurfaceItem,
    ChunkType,
    EntryPoint,
    EntryPointKind,
    HuntChunk,
    ModuleMap,
    RepoKind,
    Seed,
    SubsystemMap,
    ThreatModel,
)
from crucible.recon.synthesize import cross_subsystem_flows, owner_of, stitch_data_flows

# repo-kind -> starting attack-class checklist (VVAH's repo-kind baselines)
BASELINE_BY_KIND: dict[RepoKind, list[str]] = {
    RepoKind.WEB_API: [
        "command_injection", "sql_injection", "template_injection",
        "unsafe_deserialization", "path_traversal", "ssrf", "auth_bypass", "xxe",
    ],
    RepoKind.NATIVE: [
        "memory_oob_read", "memory_oob_write", "integer_overflow",
        "use_after_free", "format_string", "protocol_parsing",
    ],
    RepoKind.MOBILE: [
        "webview_injection", "insecure_storage", "deeplink_handling",
        "hardcoded_secret", "cert_pinning_bypass", "exported_component",
        "unsafe_deserialization",
    ],
    RepoKind.LIBRARY: [
        "api_misuse", "unsafe_deserialization", "injection_passthrough",
        "path_traversal", "supply_chain",
    ],
    RepoKind.IAC: ["misconfiguration", "exposed_secret", "excessive_permissions"],
    RepoKind.CLI: ["command_injection", "path_traversal", "argument_injection"],
    RepoKind.UNKNOWN: [
        "command_injection", "unsafe_deserialization", "path_traversal",
        "auth_bypass", "memory_oob_write",
    ],
}

# classes that cannot occur in a memory-safe language
_MEMORY_CLASSES = {
    "memory_oob_read", "memory_oob_write", "integer_overflow",
    "use_after_free", "format_string",
}
LANG_INCOMPATIBLE: dict[str, set[str]] = {
    "python": _MEMORY_CLASSES,
    "javascript": _MEMORY_CLASSES,
    "typescript": _MEMORY_CLASSES,
    "tsx": _MEMORY_CLASSES,
    "ruby": _MEMORY_CLASSES,
    "go": {"use_after_free", "format_string"},
    "java": _MEMORY_CLASSES,
}

# which classes are worth pairing with an entry point of a given kind
_KIND_CLASSES: dict[EntryPointKind, list[str]] = {
    EntryPointKind.NETWORK: [
        "protocol_parsing", "memory_oob_read", "memory_oob_write", "integer_overflow",
        "unsafe_deserialization", "ssrf", "command_injection",
    ],
    EntryPointKind.FRAMEWORK: [
        "command_injection", "sql_injection", "template_injection", "path_traversal",
        "ssrf", "auth_bypass", "xxe",
    ],
    EntryPointKind.DESERIALIZATION: ["unsafe_deserialization", "xxe"],
    EntryPointKind.FILE: ["path_traversal", "unsafe_deserialization", "xxe"],
    EntryPointKind.CLI: ["command_injection", "argument_injection", "path_traversal"],
    EntryPointKind.IPC: [
        "unsafe_deserialization", "command_injection", "exported_component", "auth_bypass",
    ],
    EntryPointKind.OTHER: [],
}

# framework marker (substring of EntryPoint.framework) -> classes to hunt there,
# overriding the repo-kind baseline (a web server inside a mobile repo, etc.)
_FRAMEWORK_CLASSES: list[tuple[str, list[str]]] = [
    ("webview", ["webview_injection", "deeplink_handling"]),
    ("deeplink", ["deeplink_handling", "webview_injection"]),
    ("bridge", ["exported_component", "unsafe_deserialization", "auth_bypass"]),
    ("express", ["command_injection", "sql_injection", "path_traversal", "ssrf", "auth_bypass"]),
    ("flask", ["command_injection", "sql_injection", "template_injection", "ssrf", "auth_bypass"]),
    ("fastapi", ["command_injection", "sql_injection", "path_traversal", "ssrf", "auth_bypass"]),
    ("django", ["sql_injection", "template_injection", "path_traversal", "auth_bypass"]),
    ("spring", ["command_injection", "sql_injection", "path_traversal", "xxe", "auth_bypass"]),
]


def _classes_for_entrypoint(ep: EntryPoint, baseline: list[str], incompat: set[str]) -> list[str]:
    """Pick attack classes for one entry point: framework marker first, then
    entry-point kind, then the repo-kind baseline. Pruned for language."""
    for marker, classes in _FRAMEWORK_CLASSES:
        if marker in (ep.framework or "").lower():
            return [c for c in classes if c not in incompat]
    kind_classes = [c for c in _KIND_CLASSES.get(ep.kind, []) if c not in incompat]
    if kind_classes:
        return kind_classes[:4]
    return baseline[:3]

_STRIDE_CLASS = {
    "spoofing": "auth_bypass",
    "tampering": "unsafe_deserialization",
    "repudiation": "auth_bypass",
    "info_disclosure": "path_traversal",
    "dos": "protocol_parsing",
    "eop": "auth_bypass",
}

# Queue tiers — the head of the hunt queue is the highest-value work Recon can
# name. `decompose` sorts on (_TIER, priority, area, class), so ranked-surface +
# taint chunks come first, then specialist, risk, threat_fallback, and the
# baseline catch_all sweep LAST (issue #35).
_TIER: dict[ChunkType, int] = {
    ChunkType.SURFACE: 0,
    ChunkType.TAINT: 0,
    ChunkType.SPECIALIST: 1,
    ChunkType.RISK: 2,
    ChunkType.THREAT_FALLBACK: 3,
    ChunkType.CATCH_ALL: 4,
}

# At most this many ranked `AttackSurfaceItem`s become SURFACE chunks, so a wide
# surface can't crowd specialist / risk chunks out of the capped queue.
SURFACE_CHUNK_LIMIT = 20

_FILE_LINE = re.compile(r"([\w./\-]+\.\w+):(\d+)")
_TRAILING_PAREN = re.compile(r"\(([^()]+)\)\s*$")

# substrings in a `SubsystemMap.dangerous_sinks` string -> the attack class it
# implies (issue #35 part 3 — "attack class from the sink kind where derivable")
_SINK_CLASS: list[tuple[tuple[str, ...], str]] = [
    (("subprocess", "popen", "os.system", "system(", "shell=true", "/bin/sh",
      "execve", "execl", "os.exec", "spawn", "child_process", "eval("),
     "command_injection"),
    (("deserial", "pickle", "yaml.load", "marshal", "unmarshal", "unserialize",
      "readobject"), "unsafe_deserialization"),
    (("sql", "query(", "execute(", "executemany", "cursor", "select ",
      "sqlalchemy", ".raw(", "sequelize"), "sql_injection"),
    (("render_template", "jinja", "template_string", "render_string", ".render("),
     "template_injection"),
    (("os.path", "path traversal", "send_file", "sendfile", "fopen", "open(",
      "writefile", "filepath"), "path_traversal"),
]


def _class_for_sink(text: str) -> str | None:
    low = (text or "").lower()
    for markers, cls in _SINK_CLASS:
        if any(m in low for m in markers):
            return cls
    return None


def _parse_fileline_kind(s: str) -> tuple[str, int, EntryPointKind] | None:
    """`'pkg/parser.go:232 cli — note'` -> `('pkg/parser.go', 232, CLI)`."""
    m = _FILE_LINE.search(s or "")
    if not m:
        return None
    kind = EntryPointKind.OTHER
    # the kind token (if any) trails the file:line — `'...:232 cli — note'`
    for tok in re.split(r"[\s—-]+", s[m.end():].lower().strip()):
        try:
            kind = EntryPointKind(tok)
            break
        except ValueError:
            continue
    return m.group(1), int(m.group(2)), kind


def _surface_chunks(
    attack_surface: list[AttackSurfaceItem] | None,
    baseline: list[str],
    incompat: set[str],
    area_of,
) -> list[HuntChunk]:
    """One SURFACE chunk per ranked `AttackSurfaceItem`: seed_path = the exact
    `file:line` target, attack class from the entry kind (+ framework), priority
    following rank order so the top-scored target is the first chunk."""
    out: list[HuntChunk] = []
    for rank, item in enumerate((attack_surface or [])[:SURFACE_CHUNK_LIMIT]):
        parsed = _parse_fileline_kind(item.entry_point) or _parse_fileline_kind(item.target)
        if parsed is None:
            continue
        file, line, kind = parsed
        fw_m = _TRAILING_PAREN.search(item.target or "")
        synthetic = EntryPoint(
            kind=kind, file=file, line=line, framework=fw_m.group(1) if fw_m else "",
        )
        classes = _classes_for_entrypoint(synthetic, baseline, incompat)
        cls = classes[0] if classes else (baseline[0] if baseline else "auth_bypass")
        out.append(HuntChunk(
            chunk_type=ChunkType.SURFACE,
            area=item.subsystem or area_of(file),
            attack_class=cls,
            scope_hint=(
                f"ranked #{rank + 1} attack surface (score {item.score:g}, "
                f"{item.exposure}): {item.rationale}"
            )[:240],
            seed_path=f"{file}:{line}",
            priority=rank,
        ))
    return out


def _area_of(path: str) -> str:
    parts = path.split("/")
    return parts[0] if len(parts) > 1 else "."


def task_cap(seed: Seed) -> int:
    n = len(seed.entry_points) * 6 + len(seed.reflection_facts) * 2 + 12
    return max(24, min(200, n))


def decompose(
    seed: Seed,
    threat_model: ThreatModel | None,
    cap: int | None = None,
    *,
    partition: list[dict] | None = None,
    subsystem_maps: list[SubsystemMap] | None = None,
    attack_surface: list[AttackSurfaceItem] | None = None,
) -> list[HuntChunk]:
    """Deterministic hunt-queue seeding. When a `partition` is supplied
    (issue #34 phase 4) the chunk `area` is the **owning subsystem name** rather
    than the first path segment, chunks in an `external_facing` subsystem get a
    priority bump, and boundary-crossing flows from the R1b maps become
    cross-subsystem `taint` chunks.

    When `attack_surface` (R1c's ranked `AttackSurfaceItem`s) is supplied
    (issue #35) each item becomes a `SURFACE` chunk anchored at its exact
    `file:line`, priority follows rank order, and the queue is tier-sorted so the
    head = ranked-surface + taint, then specialist, risk, threat_fallback, and
    the baseline `catch_all` sweep LAST (pruned for areas the surface already
    covers). Empty/absent `attack_surface` -> the pre-#35 deterministic queue."""
    cap = cap if cap is not None else task_cap(seed)
    incompat = LANG_INCOMPATIBLE.get(seed.primary_language, set())
    baseline = [c for c in BASELINE_BY_KIND.get(seed.repo_kind, []) if c not in incompat]

    external = {p["name"] for p in (partition or []) if p.get("external_facing")}

    def area_of(path: str) -> str:
        return owner_of(partition, path) if partition else _area_of(path)

    def bump(prio: int, area: str) -> int:
        return max(1, prio - 1) if area in external else prio

    chunks: list[HuntChunk] = []
    seen: set[tuple] = set()

    def add(c: HuntChunk) -> None:
        key = (c.chunk_type, c.area, c.attack_class, c.scope_hint[:60])
        if key not in seen:
            seen.add(key)
            chunks.append(c)

    # --- surface: R1c's ranked attack surface (issue #35) -----------
    # Highest hunt-relevance artifact recon produces — one chunk per ranked
    # item, priority == rank so the top-scored target is the first chunk.
    for c in _surface_chunks(attack_surface, baseline, incompat, area_of):
        add(c)

    # --- taint: boundary-crossing flows stitched by R1c -------------
    for src, sink, label in cross_subsystem_flows(subsystem_maps, partition):
        add(HuntChunk(
            chunk_type=ChunkType.TAINT, area=area_of(src.split(":")[0]),
            attack_class="injection_passthrough",
            scope_hint=f"cross-subsystem flow {label}: tainted data leaves {src} and reaches {sink}",
            seed_path=f"{src} -> {sink}", priority=1,
        ))

    # --- taint/risk: dangerous sinks each R1b subsystem owns (issue #35) ---
    # decompose already consumes cross-subsystem flows; in-subsystem sinks the
    # map agents report were being dropped.
    for sm in subsystem_maps or []:
        for raw in sm.dangerous_sinks:
            parsed = _parse_fileline_kind(raw)
            if parsed is None:
                continue
            sink_file, sink_line, _ = parsed
            area = area_of(sink_file)
            if partition and area == "misc" and sm.subsystem:
                area = sm.subsystem
            hint = f"dangerous sink owned by [{sm.subsystem or area}]: {raw.strip()}"[:220]
            cls = _class_for_sink(raw)
            if cls and cls not in incompat:
                add(HuntChunk(
                    chunk_type=ChunkType.TAINT, area=area, attack_class=cls,
                    scope_hint=hint, seed_path=f"{sink_file}:{sink_line}", priority=1,
                ))
            else:
                add(HuntChunk(
                    chunk_type=ChunkType.RISK, area=area, attack_class="dynamic_dispatch",
                    scope_hint=hint, seed_path=f"{sink_file}:{sink_line}",
                    priority=bump(3, area),
                ))

    # --- catch_all: entry point x compatible baseline class -----------
    sinks_by_file: dict[str, list[int]] = {}
    for rf in seed.reflection_facts:
        sinks_by_file.setdefault(rf.file, []).append(rf.line)

    for ep in seed.entry_points:
        area = area_of(ep.file)
        for cls in _classes_for_entrypoint(ep, baseline, incompat):
            # taint chunk if there's a dynamic sink in the same file
            near = [ln for ln in sinks_by_file.get(ep.file, []) if abs(ln - ep.line) <= 120]
            if near and cls in ("command_injection", "unsafe_deserialization", "template_injection"):
                add(HuntChunk(
                    chunk_type=ChunkType.TAINT, area=area, attack_class=cls,
                    scope_hint=f"{ep.kind.value} entry {ep.file}:{ep.line} reaches dynamic sink",
                    seed_path=f"{ep.file}:{ep.line} -> {ep.file}:{near[0]}", priority=1,
                ))
            else:
                add(HuntChunk(
                    chunk_type=ChunkType.CATCH_ALL, area=area, attack_class=cls,
                    scope_hint=f"{ep.kind.value} entry point {ep.symbol or ep.file}:{ep.line}"
                    + (f" ({ep.framework})" if ep.framework else ""),
                    seed_path=f"{ep.file}:{ep.line}", priority=bump(_priority(ep.kind, cls), area),
                ))

    # --- risk: reflection / dynamic-dispatch facts -------------------
    for rf in seed.reflection_facts:
        area = area_of(rf.file)
        add(HuntChunk(
            chunk_type=ChunkType.RISK, area=area,
            attack_class="dynamic_dispatch",
            scope_hint=f"{rf.kind} at {rf.file}:{rf.line}: {rf.snippet}",
            seed_path=f"{rf.file}:{rf.line}", priority=bump(3, area),
        ))

    # --- specialist: repo-specific classes from R2 ------------------
    if threat_model:
        for spec in threat_model.repo_specific_classes:
            add(HuntChunk(
                chunk_type=ChunkType.SPECIALIST, area="*", attack_class=spec.name,
                scope_hint=(spec.methodology or spec.rationale)[:200], priority=4,
            ))
        for t in threat_model.stride:
            add(HuntChunk(
                chunk_type=ChunkType.THREAT_FALLBACK, area="*",
                attack_class=_STRIDE_CLASS.get(t.category.lower(), "auth_bypass"),
                scope_hint=f"{t.category}: {t.description} (@ {t.entry_point})"[:200],
                priority=6,
            ))

    # --- baseline sweep: every subsystem × every baseline class ------
    # Always emitted (low priority) so coverage does not depend on the seed
    # having found an entry point. Gapfill tunes this later.
    sweep_prio = 7 if chunks else 6
    if partition:
        areas = [p["name"] for p in partition]
    else:
        areas = sorted({_area_of(f.path) for f in seed.files if f.role == "source"})

    # When R1c named a ranked attack surface, the baseline sweep is a backstop
    # only: don't dilute the queue with blind sweeps of areas the surface (or a
    # concrete entry point) already covers, and never duplicate a ranked-surface
    # chunk for the same area+class (issue #35 part 2).
    prune = bool(attack_surface)
    surface_areas = {c.area for c in chunks if c.chunk_type is ChunkType.SURFACE}
    covered_area_class = {
        (c.area, c.attack_class) for c in chunks
        if c.chunk_type in (ChunkType.SURFACE, ChunkType.TAINT, ChunkType.CATCH_ALL)
    }
    entry_areas = {area_of(ep.file) for ep in seed.entry_points}

    for area in areas[:8]:
        if (prune and area in surface_areas
                and area not in external and area not in entry_areas):
            continue
        for cls in baseline:
            if prune and (area, cls) in covered_area_class:
                continue
            add(HuntChunk(
                chunk_type=ChunkType.CATCH_ALL, area=area, attack_class=cls,
                scope_hint=f"baseline sweep of {area}/ for {cls}",
                priority=bump(sweep_prio, area),
            ))

    chunks.sort(key=lambda c: (_TIER.get(c.chunk_type, 5), c.priority, c.area, c.attack_class))
    return chunks[:cap]


def _priority(kind: EntryPointKind, cls: str) -> int:
    base = {
        EntryPointKind.NETWORK: 2, EntryPointKind.FRAMEWORK: 2,
        EntryPointKind.DESERIALIZATION: 2, EntryPointKind.IPC: 3,
        EntryPointKind.FILE: 4, EntryPointKind.CLI: 4, EntryPointKind.OTHER: 5,
    }.get(kind, 5)
    return base


# ---------------------------------------------------- architecture.md render

# The 11 target sections from issue #34. The golden-file test asserts these
# headers (structure, not prose — survives model drift).
ARCHITECTURE_SECTIONS: list[str] = [
    "## 1. Overview",
    "## 2. Build / run / test",
    "## 3. Subsystem map",
    "## 4. Entry points by kind",
    "## 5. Trust boundaries",
    "## 6. Auth / authz model",
    "## 7. Data flows (end-to-end)",
    "## 8. External dependencies / parsers",
    "## 9. Reflection / dynamic dispatch",
    "## 10. Ranked attack surface",
    "## 11. Call-graph summary",
]

_QUALITY_BANNER = {
    "seed_only": (
        "> ⚠️ **Recon degradation — `seed_only`.** The RECON model returned no "
        "usable subsystem maps; every section below is derived from the static "
        "R0 seed alone. Treat coverage as best-effort and unreviewed."
    ),
    "partial": (
        "> ⚠️ **Recon degradation — `partial`.** Some subsystem agents (or the "
        "R2 threat model) returned nothing; the synthesis below is missing their "
        "input. Sections may under-report."
    ),
}


def subsystem_rows(partition: list[dict] | None, seed: Seed) -> list[dict]:
    """Normalise a partition for section 3: name, responsibility, external flag,
    and the source-LOC it owns (from the seed)."""
    loc_by_file = {f.path: f.loc for f in seed.files if f.role == "source"}
    rows: list[dict] = []
    for sub in partition or ():
        files = [p for p in sub.get("files", ()) if p in loc_by_file]
        if not files:
            files = [
                p for p in loc_by_file
                if owner_of([sub], p) == sub.get("name")
            ]
        rows.append({
            "name": sub.get("name", "misc"),
            "responsibility": sub.get("responsibility", ""),
            "external_facing": bool(sub.get("external_facing")),
            "files": len(files),
            "loc": sum(loc_by_file.get(p, 0) for p in files),
            "depends_on": sub.get("depends_on", []),
        })
    return rows


def render_architecture(
    seed: Seed,
    subsystem_maps: list[SubsystemMap] | None = None,
    *,
    partition: list[dict] | None = None,
    module_map: ModuleMap | None = None,
    auth_model: str = "",
    attack_surface: list[AttackSurfaceItem] | None = None,
    quality: str | None = None,
) -> str:
    subsystem_maps = subsystem_maps or []
    L: list[str] = []
    L.append(f"# Architecture — {seed.repo_path}")
    L.append("")
    if quality is not None and quality != "full":
        L.append(_QUALITY_BANNER.get(quality, _QUALITY_BANNER["partial"]))
        L.append("")

    # 1 --------------------------------------------------------------
    L.append(ARCHITECTURE_SECTIONS[0])
    L.append(f"- **repo kind:** {seed.repo_kind.value}")
    L.append(f"- **primary language:** {seed.primary_language}")
    L.append(f"- **frameworks:** {', '.join(seed.frameworks) or '(none detected)'}")
    L.append(f"- **files:** {seed.stats.get('source_files', 0)} source "
             f"/ {seed.stats.get('files', 0)} total")
    L.append(f"- **recon quality:** {quality or 'n/a'}")
    L.append("")

    # 2 --------------------------------------------------------------
    L.append(ARCHITECTURE_SECTIONS[1])
    mm = module_map or ModuleMap()
    for label, seed_cmds, mm_cmds in (
        ("build", seed.build.build, mm.build),
        ("run", seed.build.run, mm.run),
        ("test", seed.build.test, mm.test),
    ):
        cmds = mm_cmds or seed_cmds
        L.append(f"- **{label}:** " + ("; ".join(cmds) if cmds else "(unknown)"))
    L.append("")

    # 3 --------------------------------------------------------------
    L.append(ARCHITECTURE_SECTIONS[2])
    rows = subsystem_rows(partition, seed)
    if not rows:
        L.append("_(single undivided unit — no subsystem partition available)_")
    for r in rows:
        tag = " · **external-facing**" if r["external_facing"] else ""
        dep = f" · depends on: {', '.join(r['depends_on'])}" if r["depends_on"] else ""
        L.append(f"- **{r['name']}** — {r['responsibility'] or '(responsibility not stated)'} "
                 f"({r['files']} files / {r['loc']} LOC){tag}{dep}")
    L.append("")

    # 4 --------------------------------------------------------------
    L.append(ARCHITECTURE_SECTIONS[3])
    by_kind: dict[str, list[str]] = {}
    for ep in seed.entry_points:
        sub = owner_of(partition, ep.file)
        by_kind.setdefault(ep.kind.value, []).append(
            f"`{ep.file}:{ep.line}` `[{sub}]`"
            + (f" — {ep.framework}" if ep.framework else "")
            + (f" — `{ep.evidence}`" if ep.evidence else "")
        )
    if not by_kind:
        L.append("_(none detected by the static seed)_")
    for kind in sorted(by_kind):
        L.append(f"### {kind}")
        L.extend(f"- {x}" for x in by_kind[kind][:40])
    L.append("")

    # 5 --------------------------------------------------------------
    L.append(ARCHITECTURE_SECTIONS[4])
    boundaries = _dedup(b for sm in subsystem_maps for b in sm.trust_boundaries)
    if not boundaries:
        boundaries = _dedup(
            f"{s['name']} ⇄ {d} (subsystem dependency)"
            for s in (partition or [])
            for d in s.get("depends_on", [])
        )
    if boundaries:
        L.extend(f"- {b}" for b in boundaries[:40])
    else:
        L.append("_(none identified)_")
    L.append("")

    # 6 --------------------------------------------------------------
    L.append(ARCHITECTURE_SECTIONS[5])
    L.append(auth_model or "_(not derived)_")
    L.append("")

    # 7 --------------------------------------------------------------
    L.append(ARCHITECTURE_SECTIONS[6])
    flows = stitch_data_flows(subsystem_maps, partition)
    if flows:
        L.extend(f"- {f}" for f in flows[:60])
    else:
        L.append("_(no data flows reported by R1b)_")
    L.append("")

    # 8 --------------------------------------------------------------
    L.append(ARCHITECTURE_SECTIONS[7])
    parsers = _dedup(p for sm in subsystem_maps for p in sm.third_party_parsers)
    ext_inputs = _dedup(e for sm in subsystem_maps for e in sm.external_inputs)
    if parsers:
        L.append("**Third-party parsers of untrusted input**")
        L.extend(f"- {p}" for p in parsers[:40])
    if ext_inputs:
        L.append("**External inputs**")
        L.extend(f"- {e}" for e in ext_inputs[:40])
    if not parsers and not ext_inputs:
        L.append("_(none reported by R1b)_")
    L.append("")

    # 9 --------------------------------------------------------------
    L.append(ARCHITECTURE_SECTIONS[8])
    if seed.reflection_facts:
        L.extend(f"- `{r.file}:{r.line}` **{r.kind}** — `{r.snippet}`"
                 for r in seed.reflection_facts[:40])
    else:
        L.append("_(no reflection / dynamic-dispatch facts in the seed)_")
    L.append("")

    # 10 -------------------------------------------------------------
    L.append(ARCHITECTURE_SECTIONS[9])
    if attack_surface:
        L.append("| # | target | subsystem | entry point | exposure | score | rationale |")
        L.append("|---|---|---|---|---|---|---|")
        for i, a in enumerate(attack_surface[:30], 1):
            L.append(f"| {i} | {a.target} | {a.subsystem} | `{a.entry_point}` | "
                     f"{a.exposure} | {a.score:g} | {a.rationale} |")
    else:
        L.append("_(no attacker-reachable entry points identified)_")
    L.append("")

    # 11 -------------------------------------------------------------
    L.append(ARCHITECTURE_SECTIONS[10])
    L.append(f"- {seed.stats.get('call_edges', 0)} name-based edges "
             "(best-effort, not resolved / not path-sensitive)")
    L.append("")
    return "\n".join(L)


def _dedup(xs) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in xs:
        x = (x or "").strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out
