"""R0 — deterministic static seed (issue #5, VVAH S0/S1).

No model call (specs.md §1.8). Produces a `Seed`: file index, repo kind,
frameworks, build/run commands, entry points classified into the 7-kind
vocabulary, reflection / dynamic-dispatch facts, and a best-effort
name-based call graph (tree-sitter).

The call graph is intentionally shallow — name-based, not resolved, not
path-sensitive. It exists to scope Hunt tasks, not to prove reachability
(that is the reachability validator's job, #11).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from crucible.recon.schema import (
    BuildInfo,
    CallEdge,
    EntryPoint,
    EntryPointKind,
    FileEntry,
    ReflectionFact,
    RepoKind,
    Seed,
)

# ------------------------------------------------------------------ file walk

_EXT_LANG = {
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby", ".php": "php",
    ".tf": "terraform", ".yaml": "yaml", ".yml": "yaml",
}
_SKIP_DIRS = {
    ".git", "node_modules", "vendor", "third_party", "third-party", ".venv", "venv",
    "dist", "build", "__pycache__", ".gradle", "Pods", ".next", "coverage",
}
_CONFIG_NAMES = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "Makefile", "CMakeLists.txt", "build.gradle", "pom.xml", "Cargo.toml", "go.mod",
    "Podfile", "Dockerfile", ".babelrc", "tsconfig.json", "AndroidManifest.xml",
}
_MAX_PARSE_BYTES = 400_000


def _role(rel: str) -> str:
    low = rel.lower()
    name = rel.rsplit("/", 1)[-1]
    if name in _CONFIG_NAMES or low.endswith((".cfg", ".ini", ".toml")):
        return "config"
    if re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|(_test\.|\.test\.|\.spec\.)", low):
        return "test"
    if re.search(r"(^|/)(vendor|node_modules|third_party)(/|$)", low):
        return "vendor"
    if re.search(r"\.min\.(js|css)$|\.pb\.|(^|/)generated(/|$)|\.g\.dart$", low):
        return "generated"
    return "source"


def _walk(repo: Path) -> list[FileEntry]:
    out: list[FileEntry] = []
    for p in sorted(repo.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(repo).as_posix()
        if any(part in _SKIP_DIRS for part in p.relative_to(repo).parts[:-1]):
            continue
        lang = _EXT_LANG.get(p.suffix.lower())
        name = p.name
        if not lang and name not in _CONFIG_NAMES:
            continue
        try:
            loc = sum(1 for _ in p.open("rb")) if p.stat().st_size < _MAX_PARSE_BYTES else 0
        except OSError:
            loc = 0
        out.append(FileEntry(path=rel, language=lang or "config", loc=loc, role=_role(rel)))
    return out


# --------------------------------------------------------------- repo kind

def _read(repo: Path, rel: str) -> str:
    p = repo / rel
    try:
        return p.read_text(errors="replace") if p.is_file() else ""
    except OSError:
        return ""


def _detect_frameworks(repo: Path, files: list[FileEntry]) -> list[str]:
    fw: set[str] = set()
    pkg = _read(repo, "package.json")
    if pkg:
        for dep in ("react-native", "expo", "express", "next", "koa", "fastify", "electron"):
            if f'"{dep}"' in pkg:
                fw.add(dep)
    reqs = _read(repo, "requirements.txt") + _read(repo, "pyproject.toml") + _read(repo, "setup.py")
    for dep in ("flask", "django", "fastapi", "tornado", "aiohttp", "bottle", "pyramid"):
        if re.search(rf"\b{dep}\b", reqs, re.I):
            fw.add(dep)
    if any(f.path.endswith("pom.xml") for f in files) and "spring" in _read(repo, "pom.xml").lower():
        fw.add("spring")
    if any(f.path == "build.gradle" for f in files) and "spring" in _read(repo, "build.gradle").lower():
        fw.add("spring")
    return sorted(fw)


def _detect_repo_kind(repo: Path, files: list[FileEntry], frameworks: list[str]) -> RepoKind:
    paths = {f.path for f in files}
    has_ios = any(p.startswith("ios/") or p.endswith(".xcodeproj/project.pbxproj") for p in paths)
    has_android = any(p.startswith("android/") or p.endswith("AndroidManifest.xml") for p in paths)
    if {"react-native", "expo"} & set(frameworks) or (has_ios and has_android):
        return RepoKind.MOBILE
    if {"flask", "django", "fastapi", "express", "next", "koa", "fastify", "spring", "tornado", "aiohttp"} & set(frameworks):
        return RepoKind.WEB_API

    src = [f for f in files if f.role == "source"]
    lang_loc = Counter()
    for f in src:
        lang_loc[f.language] += f.loc
    top = lang_loc.most_common(1)[0][0] if lang_loc else "unknown"

    if any(p in ("Makefile", "CMakeLists.txt") for p in paths) and top in ("c", "cpp"):
        return RepoKind.NATIVE
    if any(p.endswith(".tf") for p in paths) or (
        sum(1 for p in paths if p.endswith((".yaml", ".yml"))) > max(1, len(src))
    ):
        return RepoKind.IAC

    text = "\n".join(_read(repo, f.path) for f in src[:60])
    if re.search(r"\b(argparse|click\.command|cobra\.Command|commander|yargs)\b", text) and re.search(
        r"__main__|func main\(|int main\(", text
    ):
        return RepoKind.CLI
    if any(p in ("setup.py", "pyproject.toml", "package.json", "Cargo.toml", "go.mod") for p in paths):
        return RepoKind.LIBRARY
    return RepoKind.UNKNOWN


# ------------------------------------------------------------- build info

def _build_info(repo: Path) -> BuildInfo:
    bi = BuildInfo()
    pkg_raw = _read(repo, "package.json")
    if pkg_raw:
        bi.package_manager = "npm"
        try:
            scripts = json.loads(pkg_raw).get("scripts", {})
        except ValueError:
            scripts = {}
        for name, _cmd in scripts.items():
            target = bi.test if "test" in name else bi.build if name in ("build", "compile") else bi.run
            target.append(f"npm run {name}")
    mk = _read(repo, "Makefile")
    if mk:
        bi.package_manager = bi.package_manager or "make"
        for m in re.finditer(r"^([a-zA-Z][\w-]*):", mk, re.M):
            (bi.test if m.group(1) in ("test", "check") else bi.build).append(f"make {m.group(1)}")
    pyproj = _read(repo, "pyproject.toml")
    if pyproj:
        bi.package_manager = bi.package_manager or "pip"
        if "[project.scripts]" in pyproj or "[tool.poetry.scripts]" in pyproj:
            bi.run.append("(see [project.scripts])")
        bi.test.append("pytest")
    if _read(repo, "CMakeLists.txt"):
        bi.package_manager = bi.package_manager or "cmake"
        bi.build.append("cmake -B build && cmake --build build")
    return bi


# --------------------------------------------------------- entry points

# (regex, kind, framework) per language family. Regex captures nothing special;
# line number comes from the match offset.
_EP_PATTERNS: dict[str, list[tuple[str, EntryPointKind, str]]] = {
    "python": [
        (r"@(?:app|bp|blueprint|api|router)\.(?:route|get|post|put|delete|patch)\b", EntryPointKind.FRAMEWORK, "flask/fastapi"),
        (r"@(?:api_view|action)\b|class \w+\((?:generics|viewsets|APIView)", EntryPointKind.FRAMEWORK, "django-rest"),
        (r"\burlpatterns\b|\bpath\(\s*['\"]", EntryPointKind.FRAMEWORK, "django"),
        (r"\bsocket\.socket\(|\.bind\(\(|\.recv\(|asyncio\.start_server", EntryPointKind.NETWORK, ""),
        (r"\bpickle\.loads?\(|\byaml\.load\((?!.*Loader)|\bmarshal\.loads\(|__reduce__", EntryPointKind.DESERIALIZATION, ""),
        (r"\bargparse\.ArgumentParser\(|\bclick\.command\(", EntryPointKind.CLI, ""),
        (r"if __name__ == ['\"]__main__['\"]", EntryPointKind.CLI, ""),
        (r"\bopen\(\s*(?:sys\.argv|request|input)", EntryPointKind.FILE, ""),
    ],
    "javascript": [
        (r"\b(?:app|router|server)\.(?:get|post|put|delete|patch|use|all)\s*\(", EntryPointKind.FRAMEWORK, "express"),
        (r"\bhttp\.createServer\(|\bnet\.createServer\(|\bnew WebSocket\.Server", EntryPointKind.NETWORK, ""),
        (r"\baddEventListener\(\s*['\"]message['\"]|\bprocess\.on\(\s*['\"]message", EntryPointKind.IPC, ""),
        (r"\bJSON\.parse\(\s*(?:req|request|body|payload|msg)", EntryPointKind.DESERIALIZATION, ""),
        (r"\bLinking\.(?:addEventListener|getInitialURL)\(|\bLinking\.addListener", EntryPointKind.FRAMEWORK, "react-native-deeplink"),
        (r"<WebView\b|injectedJavaScript\s*=|source=\{\{?\s*uri", EntryPointKind.FRAMEWORK, "react-native-webview"),
        (r"\bNativeModules\.\w+", EntryPointKind.IPC, "react-native-bridge"),
        (r"\bfs\.(?:readFile|createReadStream)\(\s*(?:req|request|path\.join\([^)]*req)", EntryPointKind.FILE, ""),
        (r"\bprocess\.argv\b|\brequire\(['\"](?:yargs|commander)", EntryPointKind.CLI, ""),
    ],
    "c": [
        (r"\bint\s+main\s*\(", EntryPointKind.CLI, ""),
        (r"\brecv\s*\(|\brecvfrom\s*\(|\bread\s*\(\s*(?:sockfd|fd|client)", EntryPointKind.NETWORK, ""),
        (r"\bbind\s*\(|\blisten\s*\(|\baccept\s*\(", EntryPointKind.NETWORK, ""),
        (r"\bfread\s*\(|\bfgets\s*\(|\bfscanf\s*\(", EntryPointKind.FILE, ""),
        (r"\bgetenv\s*\(", EntryPointKind.OTHER, ""),
    ],
}
_EP_PATTERNS["typescript"] = _EP_PATTERNS["javascript"]
_EP_PATTERNS["tsx"] = _EP_PATTERNS["javascript"]
_EP_PATTERNS["cpp"] = _EP_PATTERNS["c"]

_REFLECTION_PATTERNS: list[tuple[str, str]] = [
    (r"\beval\s*\(|\bexec\s*\(", "eval_exec"),
    (r"\bgetattr\s*\(\s*[^,]+,\s*[^)'\"]+\)", "dynamic_getattr"),
    (r"\b__import__\s*\(|\bimportlib\.import_module\s*\(", "dynamic_import"),
    (r"\bnew Function\s*\(", "eval_exec"),
    (r"\brequire\s*\(\s*[^)'\"\s]", "dynamic_require"),
    (r"\bimport\s*\(\s*[^)'\"\s]", "dynamic_import"),
    (r"\bClass\.forName\s*\(|\.getMethod\s*\(|\.getDeclaredMethod\s*\(", "dynamic_dispatch"),
]


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _scan_source(repo: Path, files: list[FileEntry]) -> tuple[list[EntryPoint], list[ReflectionFact]]:
    eps: list[EntryPoint] = []
    refs: list[ReflectionFact] = []
    for f in files:
        if f.role in ("vendor", "generated", "config", "test"):
            continue  # test code is not attack surface
        text = _read(repo, f.path)
        if not text or len(text) > _MAX_PARSE_BYTES:
            continue
        for pat, kind, fw in _EP_PATTERNS.get(f.language, []):
            for m in re.finditer(pat, text):
                ln = _line_of(text, m.start())
                eps.append(EntryPoint(
                    kind=kind, file=f.path, line=ln, framework=fw,
                    evidence=text.splitlines()[ln - 1].strip()[:160],
                ))
        for pat, rkind in _REFLECTION_PATTERNS:
            for m in re.finditer(pat, text):
                ln = _line_of(text, m.start())
                refs.append(ReflectionFact(
                    file=f.path, line=ln, kind=rkind,
                    snippet=text.splitlines()[ln - 1].strip()[:160],
                ))
    return eps, refs


# --------------------------------------------------------- call graph (tree-sitter)

_TS_LANGS = {"python", "javascript", "typescript", "tsx", "c", "cpp", "go", "java"}
_MAX_EDGES = 5000


def _call_edges(repo: Path, files: list[FileEntry]) -> list[CallEdge]:
    try:
        from tree_sitter_language_pack import get_parser
    except Exception:
        return []
    edges: list[CallEdge] = []
    parsers: dict[str, object] = {}
    for f in files:
        if len(edges) >= _MAX_EDGES or f.role in ("vendor", "generated", "config", "test"):
            continue
        lang = "cpp" if f.language == "cpp" else f.language
        if lang not in _TS_LANGS:
            continue
        src = _read(repo, f.path)
        if not src or len(src) > _MAX_PARSE_BYTES:
            continue
        try:
            parser = parsers.get(lang) or parsers.setdefault(lang, get_parser(lang))
            tree = parser.parse(src.encode())
        except Exception:
            continue
        edges.extend(_edges_from_tree(tree.root_node, src, f.path))
    return edges[:_MAX_EDGES]


_FUNC_NODES = {
    "function_definition", "function_declaration", "method_definition",
    "function_item", "method_declaration",
}
_CALL_NODES = {"call", "call_expression", "method_invocation"}


def _edges_from_tree(root, src: str, path: str) -> list[CallEdge]:
    out: list[CallEdge] = []

    def name_of(node) -> str:
        n = node.child_by_field_name("name") or node.child_by_field_name("declarator")
        return src[n.start_byte:n.end_byte].split("(")[0].strip() if n else "<anon>"

    def callee_of(node) -> str:
        fn = node.child_by_field_name("function") or (node.children[0] if node.children else None)
        if fn is None:
            return ""
        txt = src[fn.start_byte:fn.end_byte]
        return txt.split("(")[0].strip().split(".")[-1]

    def walk(node, enclosing: str):
        cur = enclosing
        if node.type in _FUNC_NODES:
            cur = name_of(node)
        if node.type in _CALL_NODES:
            callee = callee_of(node)
            if callee and callee.isidentifier():
                out.append(CallEdge(
                    caller=enclosing, callee=callee, file=path,
                    line=node.start_point[0] + 1,
                ))
        for ch in node.children:
            walk(ch, cur)

    walk(root, "<module>")
    return out


# ------------------------------------------------------------------ public

def build_seed(repo_path: str) -> Seed:
    repo = Path(repo_path).resolve()
    files = _walk(repo)
    frameworks = _detect_frameworks(repo, files)
    repo_kind = _detect_repo_kind(repo, files, frameworks)
    entry_points, reflection_facts = _scan_source(repo, files)
    call_edges = _call_edges(repo, files)

    src = [f for f in files if f.role == "source"]
    lang_loc: Counter[str] = Counter()
    for f in src:
        lang_loc[f.language] += f.loc
    primary = lang_loc.most_common(1)[0][0] if lang_loc else "unknown"

    return Seed(
        repo_path=str(repo),
        primary_language=primary,
        repo_kind=repo_kind,
        frameworks=frameworks,
        files=files,
        entry_points=entry_points,
        reflection_facts=reflection_facts,
        call_edges=call_edges,
        build=_build_info(repo),
        stats={
            "files": len(files),
            "source_files": len(src),
            "entry_points": len(entry_points),
            "reflection_facts": len(reflection_facts),
            "call_edges": len(call_edges),
        },
    )
