"""GET /runs/{id}/artifacts[...], /architecture, /recon/*, /dedup/clusters, /log (issue #42)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse, Response

from crucible.api.artifacts import artifact_index, content_type_for, resolve_artifact
from crucible.api.deps import get_workspace, require_read
from crucible.api.errors import ApiError
from crucible.api.schemas import ArtifactMetaOut

router = APIRouter(prefix="/runs/{run_id}", tags=["artifacts"], dependencies=[Depends(require_read)])

_RECON_FILES = {
    "seed": "recon/seed.json",
    "module-map": "recon/module_map.json",
    "threat-model": "recon/threat_model.json",
    "attack-surface": "recon/attack_surface.json",
    "task-manifest": "recon/task_manifest.json",
}


@router.get("/artifacts", response_model=list[ArtifactMetaOut])
def list_artifacts(run_id: str, ws: Path = Depends(get_workspace)) -> list[ArtifactMetaOut]:
    return [ArtifactMetaOut(**m.as_dict()) for m in artifact_index(ws)]


@router.get("/artifacts/{path:path}")
def get_artifact(run_id: str, path: str, ws: Path = Depends(get_workspace)) -> Response:
    fp = resolve_artifact(ws, path)  # raises ArtifactForbidden/NotFound/TooLarge
    return Response(
        content=fp.read_bytes(),
        media_type=content_type_for(fp),
        headers={"content-disposition": "inline"},
    )


def _read_json(ws: Path, rel: str, raw: bool):
    fp = resolve_artifact(ws, rel)
    if raw:
        return Response(fp.read_bytes(), media_type="application/json")
    try:
        return json.loads(fp.read_text())
    except json.JSONDecodeError as e:
        raise ApiError(500, "artifact is not valid JSON", str(e)) from e


@router.get("/architecture", response_class=PlainTextResponse)
def get_architecture(run_id: str, ws: Path = Depends(get_workspace)) -> str:
    return resolve_artifact(ws, "architecture.md").read_text()


@router.get("/recon/{name}")
def get_recon_artifact(
    run_id: str, name: str, ws: Path = Depends(get_workspace), raw: bool = Query(False)
):
    rel = _RECON_FILES.get(name)
    if rel is None:
        raise ApiError(404, "unknown recon artifact", f"one of: {sorted(_RECON_FILES)}")
    return _read_json(ws, rel, raw)


@router.get("/dedup/clusters")
def get_dedup_clusters(run_id: str, ws: Path = Depends(get_workspace), raw: bool = Query(False)):
    return _read_json(ws, "dedup/clusters.json", raw)


@router.get("/coverage-notes/{area}", response_class=PlainTextResponse)
def get_coverage_note(run_id: str, area: str, ws: Path = Depends(get_workspace)) -> str:
    return resolve_artifact(ws, f"coverage/{area}.md").read_text()


@router.get("/log", response_class=PlainTextResponse)
def get_log(
    run_id: str, ws: Path = Depends(get_workspace), tail: int | None = Query(None, ge=1)
) -> str:
    text = resolve_artifact(ws, "run.log").read_text(errors="replace")
    if tail:
        return "\n".join(text.splitlines()[-tail:]) + "\n"
    return text
