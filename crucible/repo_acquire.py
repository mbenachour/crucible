"""Safe acquisition of a target repo from a user-supplied spec (issue #62).

Deterministic, no model call, no web-framework dependency — a pure function the
API launcher (issue #57) calls. This is the API's first action that fetches
attacker-influenced content from a user-supplied URL and shells out to `git`,
so validation happens *before* any subprocess runs:

  * scheme allowlist (`https://` only — no `git://`, `ssh://`, `file://`, `ext::…`)
  * host allowlist (default github.com/gitlab.com/bitbucket.org) + a hard block
    on IP literals, loopback, and private/link-local ranges regardless of the
    allowlist (the SSRF control, specs §10-style defense in depth)
  * a leading `-` anywhere a value reaches `git`'s argv is rejected outright,
    and every git invocation still puts a `--` separator before the URL/dest
    so even a same-shaped value can't be parsed as a flag
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from crucible.repo import git_commit

DEFAULT_ALLOWED_HOSTS = ("github.com", "gitlab.com", "bitbucket.org")

_SHORTHAND_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_REF_RE = re.compile(r"^[\w./-]{1,200}$")
_MAX_STDERR = 2000


class RepoRefError(ValueError):
    """The repo spec / ref is malformed or not allowed."""


class CloneError(RuntimeError):
    """Base for clone execution failures. `dest` is already cleaned up by the
    time this is raised."""


class CloneFailed(CloneError):
    pass


class CloneTimedOut(CloneError):
    pass


class CloneTooLarge(CloneError):
    pass


@dataclass(frozen=True)
class ClonedRepo:
    path: Path
    commit: str
    url: str


def _is_blocked_host(host: str) -> bool:
    """IP-literal / loopback / private / link-local hosts are blocked even if
    an operator's allowlist would otherwise admit them (defense in depth)."""
    if host in ("localhost", ""):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # an ordinary hostname, not an IP literal
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def resolve_repo_ref(spec: str, *, allowed_hosts: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_HOSTS) -> str:
    """`'owner/repo'` -> a github.com HTTPS URL; a full `https://` URL passed
    through after validation. Raises `RepoRefError` on anything else."""
    spec = (spec or "").strip()
    if not spec or spec.startswith("-"):
        raise RepoRefError(f"invalid repo spec: {spec!r}")

    url = f"https://github.com/{spec}.git" if _SHORTHAND_RE.match(spec) else spec

    parts = urlsplit(url)
    if parts.scheme != "https":
        raise RepoRefError(f"only https:// URLs are accepted, got scheme {parts.scheme!r}")
    if not parts.netloc or parts.netloc.startswith("-"):
        raise RepoRefError(f"invalid host: {parts.netloc!r}")

    host = (parts.hostname or "").lower()
    if _is_blocked_host(host):
        raise RepoRefError(f"host not allowed: {host!r}")
    if allowed_hosts and host not in {h.lower() for h in allowed_hosts}:
        raise RepoRefError(f"host not in the allowlist: {host!r}")

    return url


def validate_ref(ref: str | None) -> str | None:
    if ref is None:
        return None
    ref = ref.strip()
    if not ref:
        return None
    if ref.startswith("-") or not _REF_RE.match(ref):
        raise RepoRefError(f"invalid ref: {ref!r}")
    return ref


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _stripped_env() -> dict[str, str]:
    """A minimal env for the clone subprocess — no ambient GIT_* config,
    credential helpers, or proxy vars that could redirect it."""
    keep = {"PATH", "HOME"}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["GIT_TERMINAL_PROMPT"] = "0"  # never hang waiting for credentials
    return env


def clone_repo(
    spec: str,
    dest: str | Path,
    *,
    ref: str | None = None,
    timeout_s: int = 120,
    max_bytes: int = 500 * 1024 * 1024,
    allowed_hosts: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_HOSTS,
) -> ClonedRepo:
    """Validate `spec`/`ref`, shallow-clone into `dest`, enforce the size cap.

    `dest` must not already exist. On any failure `dest` is removed and a
    `CloneError` subclass is raised — the caller never has to clean up.
    """
    url = resolve_repo_ref(spec, allowed_hosts=allowed_hosts)
    ref = validate_ref(ref)

    dest = Path(dest)
    if dest.exists():
        raise CloneError(f"destination already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)

    argv = ["git", "clone", "--depth", "1", "--no-recurse-submodules", "--single-branch"]
    if ref:
        argv += ["--branch", ref]
    argv += ["--", url, str(dest)]  # `--` : nothing after this is parsed as a flag

    try:
        proc = subprocess.run(
            argv, timeout=timeout_s, env=_stripped_env(), capture_output=True, text=True, check=False,
        )
    except subprocess.TimeoutExpired as e:
        shutil.rmtree(dest, ignore_errors=True)
        raise CloneTimedOut(f"clone of {url} timed out after {timeout_s}s") from e

    if proc.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        detail = (proc.stderr or proc.stdout or "git clone failed").strip()[:_MAX_STDERR]
        raise CloneFailed(detail)

    size = _dir_size(dest)
    if size > max_bytes:
        shutil.rmtree(dest, ignore_errors=True)
        raise CloneTooLarge(f"clone of {url} is {size} bytes, over the {max_bytes} byte cap")

    return ClonedRepo(path=dest, commit=git_commit(dest), url=url)
