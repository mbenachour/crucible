"""R3 — decompose the seed + threat model into a typed Hunt queue (issue #5).

Deterministic (specs.md §1.8). `threat_model` may be `None` (R2 skipped or
failed) — decompose still works off the seed alone.

Also renders `architecture.md` from the seed + R1 contributions.
"""

from __future__ import annotations

from crucible.recon.schema import (
    ChunkType,
    EntryPointKind,
    HuntChunk,
    MapContribution,
    RepoKind,
    Seed,
    ThreatModel,
)

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
) -> list[HuntChunk]:
    cap = cap if cap is not None else task_cap(seed)
    incompat = LANG_INCOMPATIBLE.get(seed.primary_language, set())
    baseline = [c for c in BASELINE_BY_KIND.get(seed.repo_kind, []) if c not in incompat]

    chunks: list[HuntChunk] = []
    seen: set[tuple] = set()

    def add(c: HuntChunk) -> None:
        key = (c.chunk_type, c.area, c.attack_class, c.scope_hint[:60])
        if key not in seen:
            seen.add(key)
            chunks.append(c)

    # --- catch_all: entry point x compatible baseline class -----------
    sinks_by_file: dict[str, list[int]] = {}
    for rf in seed.reflection_facts:
        sinks_by_file.setdefault(rf.file, []).append(rf.line)

    for ep in seed.entry_points:
        area = _area_of(ep.file)
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
                    seed_path=f"{ep.file}:{ep.line}", priority=_priority(ep.kind, cls),
                ))

    # --- risk: reflection / dynamic-dispatch facts -------------------
    for rf in seed.reflection_facts:
        add(HuntChunk(
            chunk_type=ChunkType.RISK, area=_area_of(rf.file),
            attack_class="dynamic_dispatch",
            scope_hint=f"{rf.kind} at {rf.file}:{rf.line}: {rf.snippet}",
            seed_path=f"{rf.file}:{rf.line}", priority=3,
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

    # --- baseline sweep: every source area × every baseline class -----
    # Always emitted (low priority) so coverage does not depend on the seed
    # having found an entry point. Gapfill (Phase 2) tunes this later.
    sweep_prio = 7 if chunks else 6
    areas = sorted({_area_of(f.path) for f in seed.files if f.role == "source"})
    for area in areas[:8]:
        for cls in baseline:
            add(HuntChunk(
                chunk_type=ChunkType.CATCH_ALL, area=area, attack_class=cls,
                scope_hint=f"baseline sweep of {area}/ for {cls}",
                priority=sweep_prio,
            ))

    chunks.sort(key=lambda c: (c.priority, c.area, c.attack_class))
    return chunks[:cap]


def _priority(kind: EntryPointKind, cls: str) -> int:
    base = {
        EntryPointKind.NETWORK: 2, EntryPointKind.FRAMEWORK: 2,
        EntryPointKind.DESERIALIZATION: 2, EntryPointKind.IPC: 3,
        EntryPointKind.FILE: 4, EntryPointKind.CLI: 4, EntryPointKind.OTHER: 5,
    }.get(kind, 5)
    return base


# ---------------------------------------------------- architecture.md render

def render_architecture(seed: Seed, contributions: list[MapContribution]) -> str:
    L: list[str] = []
    L.append(f"# Architecture — {seed.repo_path}")
    L.append("")
    L.append(f"- **repo kind:** {seed.repo_kind.value}")
    L.append(f"- **primary language:** {seed.primary_language}")
    L.append(f"- **frameworks:** {', '.join(seed.frameworks) or '(none detected)'}")
    L.append(f"- **files:** {seed.stats.get('source_files', 0)} source "
             f"/ {seed.stats.get('files', 0)} total")
    L.append("")

    L.append("## Build / run")
    for label, cmds in (("build", seed.build.build), ("run", seed.build.run), ("test", seed.build.test)):
        L.append(f"- **{label}:** " + ("; ".join(cmds) if cmds else "(unknown)"))
    L.append("")

    L.append("## Entry points (by kind)")
    by_kind: dict[str, list[str]] = {}
    for ep in seed.entry_points:
        by_kind.setdefault(ep.kind.value, []).append(
            f"`{ep.file}:{ep.line}`" + (f" — {ep.framework}" if ep.framework else "")
            + (f" — `{ep.evidence}`" if ep.evidence else "")
        )
    if not by_kind:
        L.append("_(none detected by the static seed)_")
    for kind in sorted(by_kind):
        L.append(f"### {kind}")
        L.extend(f"- {x}" for x in by_kind[kind][:40])
    L.append("")

    if seed.reflection_facts:
        L.append("## Reflection / dynamic dispatch (breaks naive reachability)")
        L.extend(f"- `{r.file}:{r.line}` **{r.kind}** — `{r.snippet}`"
                 for r in seed.reflection_facts[:40])
        L.append("")

    if contributions:
        L.append("## Model-refined map")
        for c in contributions:
            L.append(f"### slice: {c.slice_name or '(unnamed)'}")
            for label, items in (
                ("entry points", c.entry_points), ("trust boundaries", c.trust_boundaries),
                ("external inputs", c.external_inputs), ("data flows", c.data_flows),
            ):
                if items:
                    L.append(f"**{label}**")
                    L.extend(f"- {i}" for i in items[:30])
            if c.notes:
                L.append(c.notes.strip())
            L.append("")

    L.append("## Call graph")
    L.append(f"- {seed.stats.get('call_edges', 0)} name-based edges "
             "(best-effort, not resolved / not path-sensitive)")
    L.append("")
    return "\n".join(L)
