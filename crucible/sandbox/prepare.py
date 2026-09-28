"""Dependency preparation for sandboxed PoC runs (issue #9).

A fresh clone has no dependencies and the sandbox has no network, so a PoC
that imports the repo's code can't run. Before Hunt, install the repo's
dependencies once into a copy of it (the "prepared tree"), in a throwaway
container. That install is the one sandbox step allowed network access, and
package lifecycle scripts stay disabled. Hunt sandboxes and the PoC gate both
mount the prepared tree read-only, so a Hunter can rehearse its PoC in exactly
the environment the gate will judge it in.

The prepared tree lives beside the workspace, never inside it: `commit_node`
runs `git add -A` on the workspace.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import shutil
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("crucible.sandbox.prepare")

NODE_IMAGE = "node:22-bookworm"      # has node, npm, corepack, python3, git
PYTHON_IMAGE = "python:3.12-bookworm"  # has python3, pip, git
MARKER = ".crucible-prepared.json"
PYDEPS_DIR = ".crucible-pydeps"
INSTALL_TIMEOUT_S = 900
# Relative entries resolve against the cwd, which is the repo root for a PoC.
PYTHONPATH = f".:src:{PYDEPS_DIR}"

_COREPACK = (
    "mkdir -p /tmp/bin && corepack enable --install-directory /tmp/bin && "
    "export PATH=/tmp/bin:$PATH && "
)


@dataclass(frozen=True)
class Ecosystem:
    name: str                 # "node" | "python" | "none"
    image: str
    install_cmd: str | None


@dataclass(frozen=True)
class PreparedRepo:
    path: str                 # host dir: the repo copy, plus installed deps
    ecosystem: str
    image: str
    installed: bool
    detail: str
    commit: str


def detect_ecosystem(repo: str | Path) -> Ecosystem:
    r = Path(repo)
    if (r / "package.json").is_file():
        if (r / "pnpm-lock.yaml").is_file():
            # The store goes in /tmp so it isn't copied into every sandbox.
            cmd = _COREPACK + "pnpm install --frozen-lockfile --ignore-scripts --store-dir /tmp/pnpm-store"
        elif (r / "yarn.lock").is_file():
            cmd = _COREPACK + (
                "(yarn install --frozen-lockfile --ignore-scripts "
                "|| YARN_ENABLE_SCRIPTS=0 yarn install --immutable)"
            )
        elif (r / "package-lock.json").is_file() or (r / "npm-shrinkwrap.json").is_file():
            cmd = "npm ci --ignore-scripts --no-audit --no-fund"
        else:
            cmd = "npm install --ignore-scripts --no-audit --no-fund"
        return Ecosystem("node", NODE_IMAGE, cmd)

    reqs = _python_requirements(r)
    if reqs is not None:
        # pytest so a PoC can be a test; the project itself is NOT installed, or
        # an installed copy would shadow the patched source on PYTHONPATH.
        cmd = (f"pip install --no-cache-dir --disable-pip-version-check "
               f"--target {PYDEPS_DIR} pytest " + " ".join(reqs))
        return Ecosystem("python", PYTHON_IMAGE, cmd)
    return Ecosystem("none", PYTHON_IMAGE, None)


def _python_requirements(r: Path) -> list[str] | None:
    """pip arguments for a Python repo's dependencies, or None if it isn't one."""
    args: list[str] = []
    for name in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt"):
        if (r / name).is_file():
            args += ["-r", name]
    pyproject = r / "pyproject.toml"
    if pyproject.is_file():
        try:
            project = tomllib.loads(pyproject.read_text()).get("project", {})
        except (tomllib.TOMLDecodeError, OSError):
            project = {}
        deps = list(project.get("dependencies") or [])
        for group in ("test", "tests", "dev"):
            deps += (project.get("optional-dependencies") or {}).get(group) or []
        args += [shlex.quote(d) for d in deps if isinstance(d, str)]
    if args or pyproject.is_file() or (r / "setup.py").is_file():
        return args
    return None


def prepared_dir_for(workspace: str | Path) -> Path:
    ws = Path(workspace).resolve()
    return ws.parent / f"{ws.name}.prepared"


def prepare_repo(
    repo: str | Path, dest: str | Path, provider, *, commit: str,
    timeout_s: int = INSTALL_TIMEOUT_S,
) -> PreparedRepo:
    """Copy `repo` to `dest` and install its dependencies there. Reuses an
    earlier successful preparation of the same commit (a resumed run)."""
    repo, dest = Path(repo), Path(dest)
    eco = detect_ecosystem(repo)
    marker = dest / MARKER

    cached = _read_marker(marker, commit, eco)
    if cached is not None:
        log.info("prepare  reusing %s (%s, installed=%s)", dest, cached.ecosystem, cached.installed)
        return cached

    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(repo, dest, symlinks=True)

    install = getattr(provider, "install_dependencies", None)
    if not eco.install_cmd:
        installed, detail = False, "no dependency manifest found"
    elif install is None:
        installed, detail = False, "this sandbox provider can't install dependencies"
    else:
        log.info("prepare  installing %s dependencies (network on, scripts off)", eco.name)
        r = install(str(dest), eco.image, eco.install_cmd, timeout_s)
        installed = r.exit_code == 0 and not r.timed_out
        detail = "installed" if installed else _tail(f"{r.stdout}\n{r.stderr}")

    prepared = PreparedRepo(str(dest), eco.name, eco.image, installed, detail, commit)
    if installed or not eco.install_cmd:
        # A failed install isn't cached, so a resumed run retries it.
        marker.write_text(json.dumps({**asdict(prepared), "install_cmd": eco.install_cmd}))
    else:
        log.warning("prepare  dependency install failed; PoCs that need them will fail: %s", detail)
    return prepared


def _read_marker(marker: Path, commit: str, eco: Ecosystem) -> PreparedRepo | None:
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text())
        if data.get("commit") != commit or data.get("install_cmd") != eco.install_cmd:
            return None
        return PreparedRepo(**{k: data[k] for k in PreparedRepo.__dataclass_fields__})
    except (ValueError, KeyError, TypeError):
        return None


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _tail(text: str, n: int = 800) -> str:
    return _ANSI.sub("", text).strip()[-n:]
