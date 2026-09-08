"""Typed artifacts for the Recon stage (issue #5).

R0 -> `Seed`            (deterministic; `recon/seed.json`)
R1 -> `MapContribution` (per slice, model; rendered to `architecture.md`)
R2 -> `ThreatModel`     (model; `recon/threat_model.json`)
R3 -> `HuntChunk[]`     (deterministic; `recon/task_manifest.json` -> pending_hunts)

Schemas fed to a model as `response_format` are kept deliberately shallow —
small open-weight models are unreliable at deep nested JSON.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class EntryPointKind(str, Enum):
    NETWORK = "network"
    FRAMEWORK = "framework"
    IPC = "ipc"
    FILE = "file"
    CLI = "cli"
    DESERIALIZATION = "deserialization"
    OTHER = "other"


class RepoKind(str, Enum):
    WEB_API = "web_api"
    NATIVE = "native"
    MOBILE = "mobile"
    LIBRARY = "library"
    IAC = "iac"
    CLI = "cli"
    UNKNOWN = "unknown"


class ChunkType(str, Enum):
    TAINT = "taint"
    RISK = "risk"
    SPECIALIST = "specialist"
    CATCH_ALL = "catch_all"
    THREAT_FALLBACK = "threat_fallback"


# --- R0: Seed --------------------------------------------------------------


class FileEntry(BaseModel):
    path: str
    language: str
    loc: int
    role: str  # source | test | vendor | generated | config


class EntryPoint(BaseModel):
    kind: EntryPointKind
    file: str
    line: int
    symbol: str = ""
    framework: str = ""
    evidence: str = ""


class ReflectionFact(BaseModel):
    file: str
    line: int
    kind: str  # eval_exec | dynamic_getattr | dynamic_require | dynamic_dispatch | dynamic_import
    snippet: str


class CallEdge(BaseModel):
    caller: str
    callee: str
    file: str
    line: int


class BuildInfo(BaseModel):
    package_manager: str = ""
    build: list[str] = Field(default_factory=list)
    run: list[str] = Field(default_factory=list)
    test: list[str] = Field(default_factory=list)


class Seed(BaseModel):
    repo_path: str
    primary_language: str
    repo_kind: RepoKind
    frameworks: list[str] = Field(default_factory=list)
    files: list[FileEntry] = Field(default_factory=list)
    entry_points: list[EntryPoint] = Field(default_factory=list)
    reflection_facts: list[ReflectionFact] = Field(default_factory=list)
    call_edges: list[CallEdge] = Field(default_factory=list)
    build: BuildInfo = Field(default_factory=BuildInfo)
    stats: dict[str, int] = Field(default_factory=dict)


# --- R1a: Module map (lead agent — the top-down read, issue #34) --------


class Subsystem(BaseModel):
    """One semantic unit of the repo — a responsibility, not a directory.

    Kept deliberately shallow: `paths` are dir/file prefixes the deterministic
    guard rail (`crucible/recon/orient.py`) resolves to a concrete file set.
    """

    name: str
    responsibility: str = ""
    paths: list[str] = Field(
        default_factory=list, description="dir or file path prefixes this subsystem owns"
    )
    external_facing: bool = Field(
        default=False, description="does attacker-controlled input reach this subsystem directly"
    )
    depends_on: list[str] = Field(default_factory=list, description="names of subsystems it calls")


class ModuleMap(BaseModel):
    """R1a output — the lead agent's top-down read of the whole repo."""

    subsystems: list[Subsystem] = Field(default_factory=list)
    build: list[str] = Field(default_factory=list)
    run: list[str] = Field(default_factory=list)
    test: list[str] = Field(default_factory=list)
    auth_model: str = Field(
        default="", description="one paragraph: how the repo authenticates / authorizes callers"
    )


# --- R1b: Subsystem map (per subsystem, model) -------------------------


class SubsystemMap(BaseModel):
    """R1b output for one subsystem. Generalises the old `MapContribution`
    (which had `slice_name` + 5 list fields) with the boundary-crossing and
    sink/auth/parser detail R1c synthesis needs."""

    subsystem: str = ""
    entry_points: list[str] = Field(
        default_factory=list,
        description="attacker-reachable entry points here as 'file:line kind — note'",
    )
    trust_boundaries: list[str] = Field(default_factory=list)
    external_inputs: list[str] = Field(default_factory=list)
    data_flows: list[str] = Field(
        default_factory=list,
        description="boundary-crossing 'A/x.py:10 -> B/y.py:88' source->sink notes, naming the neighbour",
    )
    dangerous_sinks: list[str] = Field(
        default_factory=list, description="'file:line' of exec/query/deserialize/write sinks"
    )
    auth_touchpoints: list[str] = Field(
        default_factory=list, description="where auth is checked (or conspicuously not)"
    )
    third_party_parsers: list[str] = Field(
        default_factory=list, description="libraries that parse untrusted bytes"
    )
    notes: str = ""

    @property
    def slice_name(self) -> str:  # back-compat with pre-#34 callers
        return self.subsystem


# Deprecated alias — kept so any out-of-tree import keeps resolving.
MapContribution = SubsystemMap


# --- R1c: ranked attack surface (deterministic synthesis) --------------


class AttackSurfaceItem(BaseModel):
    target: str = Field(description="what an attacker would hit, e.g. 'POST /upload handler'")
    subsystem: str = ""
    entry_point: str = Field(default="", description="'file:line kind'")
    exposure: str = Field(default="internal", description="external | internal")
    rationale: str = ""
    score: float = 0.0


# --- R2: Threat model --------------------------------------------------


class StrideThreat(BaseModel):
    entry_point: str = Field(description="'file:line' or a symbol name")
    category: str = Field(description="spoofing|tampering|repudiation|info_disclosure|dos|eop")
    description: str
    attacker: str


class AttackClassSpec(BaseModel):
    name: str
    methodology: str
    rationale: str


class ThreatModel(BaseModel):
    attackers: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)
    trust_boundaries: list[str] = Field(default_factory=list)
    stride: list[StrideThreat] = Field(default_factory=list)
    repo_specific_classes: list[AttackClassSpec] = Field(default_factory=list)


# --- R3: Hunt chunk (-> pending_hunts) -------------------------------


class HuntChunk(BaseModel):
    chunk_type: ChunkType
    area: str
    attack_class: str
    scope_hint: str
    seed_path: str | None = None  # "file:line" or "file:line -> file:line"
    priority: int = 5             # lower = sooner
