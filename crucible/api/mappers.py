"""Row → response-model mapping. Keeps ORM objects out of the routers."""

from __future__ import annotations

from crucible.api.schemas import FindingOut, RunOut, ValidationOut, WishOut
from crucible.store.models import FindingRow, Run, ValidationRow, WishRow


def run_out(r: Run, counts: dict[str, int] | None = None) -> RunOut:
    return RunOut(
        run_id=r.run_id,
        repo_path=r.repo_path,
        repo_commit=r.repo_commit,
        primary_language=r.primary_language,
        created_at=r.created_at,
        finished_at=r.finished_at,
        status=r.status,
        outcome=r.outcome or "",
        workspace_path=r.workspace_path or "",
        report_available=bool(r.report_path),
        counts=counts or {},
    )


def validation_out(v: ValidationRow) -> ValidationOut:
    return ValidationOut(
        pass_name=v.pass_name,
        verdict=v.verdict,
        reasoning=v.reasoning or "",
        model=v.model or "",
        prompt_version=v.prompt_version or "",
        response_class=v.response_class or "",
        created_at=v.created_at,
    )


def finding_out(f: FindingRow, *, trail: list[ValidationRow] | None = None) -> FindingOut:
    p = dict(f.payload or {})
    return FindingOut(
        finding_id=f.finding_id,
        run_id=f.run_id,
        stable_key=f.stable_key,
        status=f.status,
        severity=p.get("severity"),
        title=p.get("title", ""),
        file_path=p.get("file_path", ""),
        line_start=p.get("line_start"),
        line_end=p.get("line_end"),
        description=p.get("description", ""),
        threat_model=p.get("threat_model"),
        poc_test=p.get("poc_test", ""),
        proposed_patch=p.get("proposed_patch", ""),
        provenance={
            "hunter_model": f.hunter_model,
            "hunter_prompt_version": f.hunter_prompt_version,
            "hunter_sampling": f.hunter_sampling,
        },
        duplicate_of=p.get("duplicate_of"),
        created_at=f.created_at,
        validation_trail=[validation_out(v) for v in trail] if trail is not None else None,
    )


def wish_out(w: WishRow) -> WishOut:
    return WishOut(
        id=w.id,
        run_id=w.run_id,
        blocked_task_id=w.blocked_task_id,
        need=w.need,
        context=w.context or "",
        status=w.status,
        created_at=w.created_at,
    )
