"""R1a orient guard rail — deterministic partition_subsystems (issue #34)."""

from __future__ import annotations

from crucible.recon.orient import (
    fallback_partition,
    owner_index,
    partition_subsystems,
    subsystem_of,
)
from crucible.recon.schema import FileEntry, ModuleMap, RepoKind, Seed, Subsystem
from crucible.recon.seed import build_seed


def _seed(files: dict[str, int], **kw) -> Seed:
    base = {
        "repo_path": "/x",
        "primary_language": "python",
        "repo_kind": RepoKind.WEB_API,
        "files": [FileEntry(path=p, language="python", loc=n, role="source")
                  for p, n in files.items()],
    }
    base.update(kw)
    return Seed(**base)


def _all_src(seed):
    return {f.path for f in seed.files if f.role == "source"}


def _covered(seed, part):
    got = [f for p in part for f in p["files"]]
    assert sorted(got) == sorted(_all_src(seed)), "coverage broken"
    assert len(got) == len(set(got)), "double assignment"


# ------------------------------------------------------------- fallback


def test_fallback_used_when_no_module_map(repo_web):
    seed = build_seed(str(repo_web))
    part = partition_subsystems(seed, None, max_subagents=8)
    assert part and all(p["source"] == "fallback" for p in part)
    _covered(seed, part)


def test_fallback_partition_is_loc_balanced_and_covers(repo_web):
    seed = build_seed(str(repo_web))
    part = fallback_partition(seed, 3)
    _covered(seed, part)
    assert 1 <= len(part) <= 3


# ------------------------------------------------------ assignment + misc


def test_every_source_file_assigned_once_with_misc_catch_all():
    seed = _seed({
        "api/a.py": 100, "api/b.py": 100,
        "core/c.py": 100,
        "loose.py": 20,          # matches no proposed path -> misc
    })
    mm = ModuleMap(subsystems=[
        Subsystem(name="api", paths=["api"], external_facing=True),
        Subsystem(name="core", paths=["core"]),
    ])
    part = partition_subsystems(seed, mm, max_subagents=8)
    _covered(seed, part)
    names = {p["name"] for p in part}
    assert "misc" in names
    assert any(p["name"] == "api" and p["external_facing"] for p in part)


# --------------------------------------------------------------- merge


def test_tiny_subsystem_is_merged_into_dependency():
    seed = _seed({
        "web/a.py": 900, "web/b.py": 900,
        "util/x.py": 10,     # < 3% of ~1820 LOC -> merged
    })
    mm = ModuleMap(subsystems=[
        Subsystem(name="web", paths=["web"], depends_on=["util"]),
        Subsystem(name="util", paths=["util"]),
    ])
    part = partition_subsystems(seed, mm, max_subagents=8)
    _covered(seed, part)
    assert {p["name"] for p in part} == {"web"}
    assert "util/x.py" in {p["name"]: p["files"] for p in part}["web"]


# --------------------------------------------------------------- split


def test_oversized_subsystem_is_split_by_subdir():
    seed = _seed({
        "big/alpha/a.py": 500, "big/alpha/b.py": 300,
        "big/beta/c.py": 600,
        "small/s.py": 100,
    })
    mm = ModuleMap(subsystems=[
        Subsystem(name="big", paths=["big"]),
        Subsystem(name="small", paths=["small"]),
    ])
    part = partition_subsystems(seed, mm, max_subagents=8)
    _covered(seed, part)
    names = {p["name"] for p in part}
    # "big" held ~93% of LOC -> split into big/alpha + big/beta
    assert any(n.startswith("big/") for n in names)
    assert not any(p["name"] == "big" for p in part)


# ------------------------------------------------------------- clamp N


def test_count_clamped_to_max_subagents_nothing_dropped():
    files = {f"m{i}/f.py": 100 for i in range(12)}
    seed = _seed(files)
    mm = ModuleMap(subsystems=[
        Subsystem(name=f"m{i}", paths=[f"m{i}"]) for i in range(12)
    ])
    part = partition_subsystems(seed, mm, max_subagents=8)
    assert 1 <= len(part) <= 8
    _covered(seed, part)


def test_single_subsystem_allowed():
    seed = _seed({"only/a.py": 50, "only/b.py": 50})
    mm = ModuleMap(subsystems=[Subsystem(name="only", paths=["only"])])
    part = partition_subsystems(seed, mm, max_subagents=8)
    assert len(part) == 1
    _covered(seed, part)


# ------------------------------------------------------- reverse lookup


def test_owner_index_and_subsystem_of():
    part = [
        {"name": "api", "paths": ["api", "gateway"], "files": []},
        {"name": "core", "paths": ["core"], "files": ["core/db.py"]},
    ]
    idx = owner_index(part)
    assert subsystem_of(idx, "api/routes/users.py") == "api"
    assert subsystem_of(idx, "gateway/mw.py") == "api"
    assert subsystem_of(idx, "core/db.py") == "core"
    assert subsystem_of(idx, "scripts/deploy.sh") == "misc"


def test_manifest_locations_used_when_no_llm_map():
    seed = Seed(
        repo_path="/x", primary_language="javascript", repo_kind=RepoKind.WEB_API,
        files=[
            FileEntry(path="packages/api/index.js", language="javascript", loc=200, role="source"),
            FileEntry(path="packages/api/package.json", language="json", loc=10, role="config"),
            FileEntry(path="packages/ui/app.js", language="javascript", loc=150, role="source"),
            FileEntry(path="packages/ui/package.json", language="json", loc=10, role="config"),
        ],
    )
    part = partition_subsystems(seed, None, max_subagents=8)
    assert all(p["source"] == "manifests" for p in part)
    assert {p["name"] for p in part} == {"packages_api", "packages_ui"}
    _covered(seed, part)
