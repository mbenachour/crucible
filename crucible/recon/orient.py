"""R1a orient — the deterministic guard rail around the lead agent (issue #34).

`_run_orient` (in the graph node) asks one budgeted lead agent to read the repo
top-down and emit a typed `ModuleMap`. Everything in *this* module is pure and
model-free: it turns whatever the agent proposed (or nothing) into a concrete,
coverage-guaranteed **partition** — a list of

    {name, responsibility, external_facing, paths, files, depends_on, loc}

Boundary-source priority (issue #34): (1) the LLM `ModuleMap`, (2) monorepo
package-manifest locations, (3) `CODEOWNERS`, (4) `fallback_partition` (the
pre-#34 LOC-balanced directory slicing).

Validation before a partition is returned:
  * every `role=="source"` file maps to exactly one subsystem (unassigned -> `misc`)
  * a subsystem holding > ~40 % of source LOC is split by sub-directory
  * a subsystem holding < ~3 % of source LOC is merged into a `depends_on`
    sibling (or the largest)
  * N is clamped to `[1, RECON_MAX_SUBAGENTS]` by merging the smallest first
"""

from __future__ import annotations

import re
from pathlib import Path

from crucible.recon.schema import ModuleMap, Seed

SPLIT_LOC_FRACTION = 0.40
MERGE_LOC_FRACTION = 0.03

_MANIFESTS = {
    "package.json", "pyproject.toml", "setup.py", "go.mod", "Cargo.toml",
    "pom.xml", "build.gradle", "build.gradle.kts", "composer.json", "Gemfile",
}


# --------------------------------------------------------------- helpers


def _source_files(seed: Seed) -> list[tuple[str, int]]:
    return [(f.path, f.loc) for f in seed.files if f.role == "source"]


def _area(path: str) -> str:
    parts = path.split("/")
    return parts[0] if len(parts) > 1 else "."


def _norm(name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9._/-]+", "_", (name or "").strip()).strip("_/")
    return name or "misc"


def owner_index(partition: list[dict]) -> list[tuple[str, str]]:
    """(prefix, subsystem-name) pairs, longest prefix first — for reverse lookup."""
    pairs: list[tuple[str, str]] = []
    for sub in partition:
        for pref in list(sub.get("paths", ())) + list(sub.get("files", ())):
            pairs.append((pref.rstrip("/"), sub["name"]))
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


def subsystem_of(index: list[tuple[str, str]], path: str) -> str:
    for pref, name in index:
        if path == pref or path.startswith(pref + "/"):
            return name
    return "misc"


# ---------------------------------------------------- boundary sources


def _candidates_from_module_map(module_map: ModuleMap | None) -> list[dict]:
    if module_map is None:
        return []
    out: list[dict] = []
    for s in module_map.subsystems:
        paths = [p.strip().lstrip("./").rstrip("/") for p in s.paths if p.strip()]
        paths = [p for p in paths if p and p != "."]
        if not paths and not s.name:
            continue
        out.append({
            "name": _norm(s.name),
            "responsibility": (s.responsibility or "").strip(),
            "external_facing": bool(s.external_facing),
            "paths": paths or [_norm(s.name)],
            "depends_on": [_norm(d) for d in s.depends_on],
        })
    return out


def _candidates_from_manifests(seed: Seed) -> list[dict]:
    dirs: set[str] = set()
    for f in seed.files:
        base = f.path.rsplit("/", 1)[-1]
        if base in _MANIFESTS and "/" in f.path:
            dirs.add(f.path.rsplit("/", 1)[0])
    # drop nested manifest dirs that live under another manifest dir
    roots = sorted(dirs)
    keep = [d for d in roots if not any(d != o and d.startswith(o + "/") for o in roots)]
    return [
        {"name": _norm(d.replace("/", "_")), "responsibility": f"package at {d}/",
         "external_facing": False, "paths": [d], "depends_on": []}
        for d in keep
    ]


def _candidates_from_codeowners(repo_path: str | None) -> list[dict]:
    if not repo_path:
        return []
    for rel in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        p = Path(repo_path) / rel
        if p.is_file():
            break
    else:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for line in p.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        glob = line.split()[0].lstrip("/").rstrip("/*").rstrip("/")
        if not glob or glob in seen or glob in (".", "*"):
            continue
        seen.add(glob)
        out.append({"name": _norm(glob.replace("/", "_")), "responsibility": "CODEOWNERS entry",
                    "external_facing": False, "paths": [glob], "depends_on": []})
    return out


def fallback_partition(seed: Seed, n: int = 3) -> list[dict]:
    """The pre-#34 `_slice_repo`: group source files by top-level area, pack
    areas into <= n LOC-balanced buckets. Coverage guaranteed."""
    by_area: dict[str, list[tuple[str, int]]] = {}
    for path, loc in _source_files(seed):
        by_area.setdefault(_area(path), []).append((path, loc))
    if not by_area:
        return []
    areas = sorted(by_area, key=lambda a: -sum(loc for _, loc in by_area[a]))
    k = max(1, min(n, len(areas)))
    buckets: list[list[str]] = [[] for _ in range(k)]
    load = [0] * k
    for a in areas:
        i = load.index(min(load))
        buckets[i].append(a)
        load[i] += sum(loc for _, loc in by_area[a])
    out: list[dict] = []
    for b in buckets:
        if not b:
            continue
        files = [p for a in b for p, _ in by_area[a]]
        out.append({
            "name": "+".join(b), "responsibility": "", "external_facing": False,
            "paths": list(b), "files": files,
            "loc": sum(loc for a in b for _, loc in by_area[a]), "depends_on": [],
        })
    return out


# ---------------------------------------------------- the partitioner


def _assign(candidates: list[dict], src: list[tuple[str, int]]) -> list[dict]:
    """Longest-prefix file -> candidate assignment; unclaimed -> `misc`."""
    index: list[tuple[str, str, int]] = []
    for ci, c in enumerate(candidates):
        for pref in c["paths"]:
            index.append((pref.rstrip("/"), c["name"], ci))
    index.sort(key=lambda t: -len(t[0]))

    buckets: dict[str, dict] = {}
    for c in candidates:
        buckets[c["name"]] = {**c, "files": [], "loc": 0}

    for path, loc in src:
        owner = None
        for pref, name, _ci in index:
            if path == pref or path.startswith(pref + "/"):
                owner = name
                break
        if owner is None:
            owner = "misc"
            buckets.setdefault("misc", {
                "name": "misc", "responsibility": "unassigned files",
                "external_facing": False, "paths": [], "depends_on": [],
                "files": [], "loc": 0,
            })
        buckets[owner]["files"].append(path)
        buckets[owner]["loc"] += loc

    return [b for b in buckets.values() if b["files"]]


def _split_oversized(parts: list[dict], total: int) -> list[dict]:
    if total <= 0:
        return parts
    out: list[dict] = []
    for p in parts:
        if p["loc"] <= SPLIT_LOC_FRACTION * total or len(p["files"]) < 2:
            out.append(p)
            continue
        # split by the path segment just below the subsystem's common root
        root = _common_root(p["files"])
        by_sub: dict[str, list[str]] = {}
        for f in p["files"]:
            rest = f[len(root):].lstrip("/") if root else f
            seg = rest.split("/", 1)[0] if "/" in rest else "_root"
            by_sub.setdefault(seg, []).append(f)
        if len(by_sub) < 2:
            out.append(p)
            continue
        locmap = p.get("_locmap", {})
        for seg, files in sorted(by_sub.items()):
            sloc = sum(locmap.get(f, 0) for f in files) or len(files)
            out.append({
                "name": f"{p['name']}/{seg}", "responsibility": p["responsibility"],
                "external_facing": p["external_facing"],
                "paths": sorted({f.rsplit('/', 1)[0] for f in files if '/' in f} or set(files)),
                "files": files, "loc": sloc, "depends_on": p["depends_on"],
            })
    return out


def _common_root(files: list[str]) -> str:
    if not files:
        return ""
    parts = [f.split("/")[:-1] for f in files]
    root = parts[0]
    for pp in parts[1:]:
        i = 0
        while i < len(root) and i < len(pp) and root[i] == pp[i]:
            i += 1
        root = root[:i]
    return "/".join(root)


def _merge_small(parts: list[dict], total: int, floor_frac: float) -> list[dict]:
    if len(parts) <= 1 or total <= 0:
        return parts
    parts = sorted(parts, key=lambda p: -p["loc"])
    changed = True
    while changed and len(parts) > 1:
        changed = False
        for i, p in enumerate(parts):
            if p["loc"] >= floor_frac * total:
                continue
            target = _merge_target(p, parts, i)
            if target is None:
                continue
            target["files"].extend(p["files"])
            target["loc"] += p["loc"]
            target["paths"] = sorted(set(target["paths"]) | set(p["paths"]))
            parts.pop(i)
            parts.sort(key=lambda q: -q["loc"])
            changed = True
            break
    return parts


def _merge_target(p: dict, parts: list[dict], i: int) -> dict | None:
    for dep in p.get("depends_on", []):
        for q in parts:
            if q is not p and q["name"] == dep:
                return q
    others = [q for j, q in enumerate(parts) if j != i]
    return max(others, key=lambda q: q["loc"]) if others else None


def partition_subsystems(
    seed: Seed,
    module_map: ModuleMap | None,
    *,
    max_subagents: int,
    repo_path: str | None = None,
) -> list[dict]:
    """Turn a (possibly absent / partial) `ModuleMap` into a coverage-guaranteed
    partition. See the module docstring for the rules."""
    src = _source_files(seed)
    if not src:
        return []
    total = sum(loc for _, loc in src)
    locmap = dict(src)

    candidates = _candidates_from_module_map(module_map)
    source = "llm"
    if not candidates:
        candidates = _candidates_from_manifests(seed)
        source = "manifests"
    if not candidates:
        candidates = _candidates_from_codeowners(repo_path)
        source = "codeowners"
    if not candidates:
        parts = fallback_partition(seed, min(max_subagents, 3))
        for p in parts:
            p["source"] = "fallback"
        return _finalize(parts, total, max_subagents)

    parts = _assign(candidates, src)
    for p in parts:
        p["_locmap"] = locmap
        p["source"] = source
    parts = _split_oversized(parts, total)
    parts = _merge_small(parts, total, MERGE_LOC_FRACTION)
    return _finalize(parts, total, max_subagents)


def _finalize(parts: list[dict], total: int, max_subagents: int) -> list[dict]:
    # clamp N by merging the smallest until we fit
    while len(parts) > max(1, max_subagents):
        parts.sort(key=lambda p: p["loc"])
        small = parts.pop(0)
        tgt = _merge_target(small, parts, -1) or parts[0]
        tgt["files"].extend(small["files"])
        tgt["loc"] += small["loc"]
        tgt["paths"] = sorted(set(tgt["paths"]) | set(small["paths"]))
    for p in parts:
        p.pop("_locmap", None)
        p["files"] = sorted(set(p["files"]))
        p["paths"] = sorted(set(p["paths"]))
        p.setdefault("responsibility", "")
        p.setdefault("external_facing", False)
        p.setdefault("depends_on", [])
    parts.sort(key=lambda p: (-p["loc"], p["name"]))
    return parts
