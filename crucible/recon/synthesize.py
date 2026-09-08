"""R1c — deterministic synthesis (issue #34).

Takes the structured R1a `ModuleMap` + R1b `SubsystemMap`s + the R0 `Seed`
(+ optional R2 `ThreatModel`) and produces the cross-cutting content no single
subsystem agent sees:

  * a consolidated auth/authz model            `derive_auth_model`
  * end-to-end data flows stitched across boundaries  `stitch_data_flows`
  * a ranked attack surface                     `rank_attack_surface`

Everything here is pure and model-free — it must run (and be tested) with no
registry. When the R1b maps are empty it degrades to ranking the static seed
entry points alone ("rank by entry-kind severity x count").
"""

from __future__ import annotations

import re

from crucible.recon.schema import (
    AttackSurfaceItem,
    EntryPoint,
    EntryPointKind,
    ModuleMap,
    Seed,
    SubsystemMap,
    ThreatModel,
)

# entry-point kind -> severity weight (network/framework/deser are the sharp ones)
_KIND_SEVERITY: dict[EntryPointKind, float] = {
    EntryPointKind.NETWORK: 3.0,
    EntryPointKind.FRAMEWORK: 3.0,
    EntryPointKind.DESERIALIZATION: 3.0,
    EntryPointKind.IPC: 2.0,
    EntryPointKind.FILE: 2.0,
    EntryPointKind.CLI: 2.0,
    EntryPointKind.OTHER: 1.0,
}
_INHERENTLY_EXTERNAL = {EntryPointKind.NETWORK, EntryPointKind.FRAMEWORK}

_AUTH_MARKERS = (
    "jwt", "oauth", "oauth2", "bearer token", "bearer ", "session cookie", "session id",
    "login_required", "login required", "authenticate", "authorization header",
    "authorization:", "passport", "api key", "api_key", "apikey", "rbac", "role-based",
    "csrf", "basic auth", "hmac", "saml", "openid", "requires_auth", "permission_classes",
    "@auth", "access token", "refresh token", "set-cookie",
)

_FILE_LINE = re.compile(r"([\w./\-]+\.\w+):(\d+)")


# --------------------------------------------------------------- ownership


def owner_of(partition: list[dict] | None, path: str) -> str:
    """Resolve a repo-relative file path to the subsystem that owns it.

    Exact `files` membership wins; otherwise the longest matching path prefix
    from either `files` or `paths`; otherwise ``misc``.
    """
    if not partition:
        return "misc"
    best_name, best_len = "misc", -1
    for sub in partition:
        name = sub.get("name", "misc")
        for f in sub.get("files", ()):
            if f == path:
                return name
        for pref in list(sub.get("paths", ())) + list(sub.get("files", ())):
            p = pref.rstrip("/")
            if (path == p or path.startswith(p + "/")) and len(p) > best_len:
                best_name, best_len = name, len(p)
    return best_name


def _external_facing(partition: list[dict] | None, name: str) -> bool:
    for sub in partition or ():
        if sub.get("name") == name:
            return bool(sub.get("external_facing"))
    return False


# --------------------------------------------------------------- auth model


def derive_auth_model(
    seed: Seed,
    subsystem_maps: list[SubsystemMap] | None,
    module_map: ModuleMap | None,
) -> str:
    """One consolidated paragraph. Prefers the lead agent's prose; otherwise
    assembles a signal list from the R1b maps + seed and is explicit when it
    finds nothing (so downstream treats every entry point as unauthenticated)."""
    if module_map is not None and module_map.auth_model.strip():
        return module_map.auth_model.strip()

    touchpoints: list[str] = []
    haystack: list[str] = list(seed.frameworks)
    for sm in subsystem_maps or ():
        touchpoints.extend(t.strip() for t in sm.auth_touchpoints if t.strip())
        if sm.notes:
            haystack.append(sm.notes)
        haystack.extend(sm.trust_boundaries)
    for ep in seed.entry_points:
        haystack.append(f"{ep.framework} {ep.evidence}")

    blob = " \n".join(haystack).lower()
    markers = sorted({m.strip() for m in _AUTH_MARKERS if m in blob})

    if not markers and not touchpoints:
        return (
            "No authentication or authorization mechanism was identified by Recon. "
            "Every entry point in this document should be treated as reachable by an "
            "unauthenticated attacker until proven otherwise."
        )
    parts = ["Recon assembled this auth/authz model from partial signals (verify against code)."]
    if markers:
        parts.append("Markers seen: " + ", ".join(markers) + ".")
    if touchpoints:
        uniq = sorted(set(touchpoints))
        parts.append("Touchpoints: " + "; ".join(uniq[:12]) + ".")
    return " ".join(parts)


# --------------------------------------------------------------- data flows


def _flow_endpoints(flow: str) -> list[str]:
    return [f"{m.group(1)}:{m.group(2)}" for m in _FILE_LINE.finditer(flow)]


def stitch_data_flows(
    subsystem_maps: list[SubsystemMap] | None,
    partition: list[dict] | None,
) -> list[str]:
    """All R1b data flows, boundary-crossing ones first and tagged `(A -> B)`.
    A flow crosses a boundary when its two `file:line` endpoints resolve to
    different subsystems (or, lacking endpoints, it names two subsystems)."""
    names = {s.get("name") for s in partition or ()}
    crossing: list[str] = []
    internal: list[str] = []
    seen: set[str] = set()
    for sm in subsystem_maps or ():
        for raw in sm.data_flows:
            flow = raw.strip()
            if not flow or flow in seen:
                continue
            seen.add(flow)
            eps = _flow_endpoints(flow)
            subs = {owner_of(partition, e.split(":")[0]) for e in eps}
            if len(eps) < 2:
                mentioned = {n for n in names if n and n in flow}
                if len(mentioned) >= 2:
                    crossing.append(f"({' -> '.join(sorted(mentioned))}) {flow}")
                else:
                    internal.append(f"[{sm.subsystem}] {flow}")
            elif len(subs) >= 2:
                crossing.append(f"({' -> '.join(sorted(s for s in subs if s))}) {flow}")
            else:
                internal.append(f"[{sm.subsystem}] {flow}")
    return crossing + internal


def cross_subsystem_flows(
    subsystem_maps: list[SubsystemMap] | None,
    partition: list[dict] | None,
) -> list[tuple[str, str, str]]:
    """`(src_fileline, sink_fileline, 'A -> B')` for every boundary-crossing
    flow that has two concrete endpoints. Consumed by R3 to emit taint chunks."""
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for sm in subsystem_maps or ():
        for raw in sm.data_flows:
            eps = _flow_endpoints(raw)
            if len(eps) < 2:
                continue
            src, sink = eps[0], eps[-1]
            a, b = owner_of(partition, src.split(":")[0]), owner_of(partition, sink.split(":")[0])
            if a == b or (src, sink) in seen:
                continue
            seen.add((src, sink))
            out.append((src, sink, f"{a} -> {b}"))
    return out


# --------------------------------------------------------- attack surface


def _sink_files(seed: Seed) -> dict[str, list[int]]:
    by_file: dict[str, list[int]] = {}
    for rf in seed.reflection_facts:
        by_file.setdefault(rf.file, []).append(rf.line)
    return by_file


def _tm_entry_points(threat_model: ThreatModel | None) -> str:
    if not threat_model:
        return ""
    return " \n".join(t.entry_point for t in threat_model.stride).lower()


def _iter_entry_points(
    seed: Seed, subsystem_maps: list[SubsystemMap] | None
) -> list[tuple[EntryPoint, str | None]]:
    """Seed entry points, plus ones an R1b map named that the seed missed
    (parsed loosely from its `entry_points` strings). Second tuple item is the
    subsystem name when it came from a map."""
    out: list[tuple[EntryPoint, str | None]] = [(ep, None) for ep in seed.entry_points]
    known = {(ep.file, ep.line) for ep in seed.entry_points}
    for sm in subsystem_maps or ():
        for line in sm.entry_points:
            m = _FILE_LINE.search(line)
            if not m:
                continue
            key = (m.group(1), int(m.group(2)))
            if key in known:
                continue
            known.add(key)
            kind = EntryPointKind.OTHER
            low = line.lower()
            for k in EntryPointKind:
                if k.value in low:
                    kind = k
                    break
            out.append((
                EntryPoint(kind=kind, file=key[0], line=key[1], evidence=line.strip()[:120]),
                sm.subsystem,
            ))
    return out


def rank_attack_surface(
    seed: Seed,
    subsystem_maps: list[SubsystemMap] | None = None,
    module_map: ModuleMap | None = None,
    threat_model: ThreatModel | None = None,
    partition: list[dict] | None = None,
) -> list[AttackSurfaceItem]:
    """score = exposure(external>internal) x entry-kind severity
              x sink-proximity x threat-model corroboration.

    Pure and deterministic. With no `subsystem_maps` this still ranks the seed
    entry points (exposure/severity/sink-proximity/corroboration all derivable
    from R0 + R2) — the issue's "rank by entry-kind severity x count" fallback,
    generalised."""
    sinks = _sink_files(seed)
    tm_blob = _tm_entry_points(threat_model)
    sink_subsystems = {
        sm.subsystem for sm in subsystem_maps or () if sm.dangerous_sinks
    }

    items: list[AttackSurfaceItem] = []
    for ep, from_sub in _iter_entry_points(seed, subsystem_maps):
        sub = from_sub or owner_of(partition, ep.file)
        kind_sev = _KIND_SEVERITY.get(ep.kind, 1.0)

        external = (
            ep.kind in _INHERENTLY_EXTERNAL
            or _external_facing(partition, sub)
        )
        exposure_w = 2.0 if external else 1.0

        sink_prox = 1.0
        near = [ln for ln in sinks.get(ep.file, []) if abs(ln - ep.line) <= 120]
        if near:
            sink_prox += 1.0
        if sub in sink_subsystems:
            sink_prox += 0.5
        sink_prox = min(sink_prox, 2.5)

        tm_corr = 1.0
        if tm_blob and (f"{ep.file}:{ep.line}".lower() in tm_blob
                        or (ep.symbol and ep.symbol.lower() in tm_blob)):
            tm_corr = 2.0

        score = round(exposure_w * kind_sev * sink_prox * tm_corr, 2)
        why = [
            f"{'external' if external else 'internal'} exposure",
            f"{ep.kind.value} entry (sev {kind_sev:g})",
        ]
        if near:
            why.append(f"dynamic sink within 120 lines ({ep.file}:{near[0]})")
        if tm_corr > 1.0:
            why.append("corroborated by the threat model")
        items.append(AttackSurfaceItem(
            target=f"{ep.symbol or ep.file}:{ep.line}"
            + (f" ({ep.framework})" if ep.framework else ""),
            subsystem=sub,
            entry_point=f"{ep.file}:{ep.line} {ep.kind.value}",
            exposure="external" if external else "internal",
            rationale="; ".join(why),
            score=score,
        ))

    items.sort(key=lambda x: (-x.score, x.subsystem, x.entry_point))
    return items
