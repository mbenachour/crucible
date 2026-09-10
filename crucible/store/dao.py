"""Data-access helpers over store/models.py.

Deterministic code does deterministic work (§1.8) — dedup pre-filtering,
stable cross-run keys, provenance recording all live here, not in prompts.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import case, create_engine, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from crucible.store.models import (
    Base,
    FindingRow,
    Run,
    ToolUsageRow,
    ValidationRow,
    WishRow,
)

# Columns added after the first `findings.sqlite` shipped (issue #38). SQLite
# supports cheap ADD COLUMN, so an existing DB is migrated in place — no Alembic.
_RUNS_ADDED_COLUMNS = {
    "workspace_path": "VARCHAR DEFAULT ''",
    "finished_at": "DATETIME",
    "outcome": "VARCHAR DEFAULT ''",
    "report_path": "VARCHAR DEFAULT ''",
}

_MAX_LIMIT = 1000
_DEFAULT_LIMIT = 100


def _clamp(limit: int | None, offset: int | None) -> tuple[int, int]:
    lim = _DEFAULT_LIMIT if limit is None else max(1, min(int(limit), _MAX_LIMIT))
    off = max(0, int(offset or 0))
    return lim, off


class Store:
    def __init__(self, url: str = "sqlite:///findings.sqlite"):
        self.engine = create_engine(url, future=True)
        Base.metadata.create_all(self.engine)
        self._ensure_schema()
        self._Session = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def _ensure_schema(self) -> None:
        """Backfill columns added to an already-created DB (issue #38)."""
        try:
            existing = {c["name"] for c in inspect(self.engine).get_columns("runs")}
        except Exception:  # noqa: BLE001 — a brand-new DB has no table yet; create_all handled it
            return
        missing = {k: v for k, v in _RUNS_ADDED_COLUMNS.items() if k not in existing}
        if not missing:
            return
        with self.engine.begin() as conn:
            for name, ddl in missing.items():
                conn.execute(text(f"ALTER TABLE runs ADD COLUMN {name} {ddl}"))

    @contextmanager
    def session(self) -> Session:
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    @contextmanager
    def read_session(self) -> Session:
        """Read-only session — no commit on exit. Used by the API read layer."""
        s = self._Session()
        try:
            yield s
        finally:
            s.close()

    # --- writes ----------------------------------------------------------
    def create_run(
        self,
        run_id: str,
        repo_path: str,
        repo_commit: str,
        lang: str,
        workspace_path: str = "",
    ) -> None:
        with self.session() as s:
            s.add(Run(run_id=run_id, repo_path=repo_path, repo_commit=repo_commit,
                      primary_language=lang, workspace_path=workspace_path))

    def finish_run(self, run_id: str, outcome: str, report_path: str = "") -> None:
        """Record how a run ended (issue #38). Idempotent."""
        with self.session() as s:
            r = s.get(Run, run_id)
            if r is None:
                return
            r.outcome = outcome
            r.status = "finished"
            r.finished_at = datetime.now(UTC)
            if report_path:
                r.report_path = report_path

    def add_finding(self, row: FindingRow) -> None:
        with self.session() as s:
            s.add(row)

    def record_validation(self, row: ValidationRow) -> None:
        with self.session() as s:
            s.add(row)

    def set_finding_status(self, finding_id: str, status: str) -> None:
        with self.session() as s:
            f = s.get(FindingRow, finding_id)
            if f:
                f.status = status

    def link_duplicate(self, dup_id: str, canonical_id: str) -> None:
        """Fold a duplicate into its canonical (§11 Dedup). Status → `duplicate`;
        the canonical pointer rides in the payload so no schema migration is
        needed on an existing `findings.sqlite`."""
        with self.session() as s:
            f = s.get(FindingRow, dup_id)
            if f:
                f.status = "duplicate"
                f.payload = {**(f.payload or {}), "duplicate_of": canonical_id}

    def add_wish(self, row: WishRow) -> None:
        with self.session() as s:
            s.add(row)

    def flush_tool_usage(self, rows: list[dict]) -> None:
        with self.session() as s:
            s.add_all(ToolUsageRow(**r) for r in rows)

    # --- reads ---------------------------------------------------------
    def get_finding(self, finding_id: str) -> FindingRow | None:
        with self.session() as s:
            return s.get(FindingRow, finding_id)

    def run_findings(self, run_id: str, statuses: list[str] | None = None) -> list[FindingRow]:
        """Every finding for a run, optionally filtered to a set of funnel statuses."""
        with self.session() as s:
            stmt = select(FindingRow).where(FindingRow.run_id == run_id)
            if statuses:
                stmt = stmt.where(FindingRow.status.in_(statuses))
            return list(s.scalars(stmt))

    def other_run_findings(self, run_id: str, limit: int = 500) -> list[FindingRow]:
        """Findings from *earlier* runs — the cross-run dedup corpus (§11)."""
        with self.session() as s:
            return list(s.scalars(
                select(FindingRow)
                .where(FindingRow.run_id != run_id)
                .order_by(FindingRow.created_at.desc())
                .limit(limit)
            ))

    def finding_reasons(self, finding_id: str, pass_name: str) -> list[str]:
        """Validation reasoning strings recorded for one finding on one pass."""
        with self.session() as s:
            rows = s.scalars(
                select(ValidationRow).where(
                    ValidationRow.finding_id == finding_id,
                    ValidationRow.pass_name == pass_name,
                )
            )
            return [r.reasoning for r in rows if r.reasoning]

    def upheld_findings(self, run_id: str) -> list[FindingRow]:
        """Findings that survived BOTH bug and reachability passes."""
        with self.session() as s:
            return list(s.scalars(
                select(FindingRow).where(
                    FindingRow.run_id == run_id,
                    FindingRow.status == "reach_upheld",
                )
            ))

    def tool_usage(self, run_id: str) -> list[ToolUsageRow]:
        with self.session() as s:
            return list(s.scalars(
                select(ToolUsageRow).where(ToolUsageRow.run_id == run_id)
            ))

    # --- API read layer (issue #37) ------------------------------------
    #
    # Paginated, filterable reads for the HTTP API. Every list method returns
    # ``(rows, total)`` so the caller can render a page envelope. Rows are
    # detached (``expire_on_commit=False``) — the endpoint layer maps them to
    # Pydantic; ORM objects never cross the API boundary.

    def list_runs(
        self,
        *,
        repo: str | None = None,
        outcome: str | None = None,
        language: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[Run], int]:
        lim, off = _clamp(limit, offset)
        with self.read_session() as s:
            stmt = select(Run)
            if repo:
                stmt = stmt.where(Run.repo_path.like(f"%{repo}%"))
            if outcome:
                stmt = stmt.where(Run.outcome == outcome)
            if language:
                stmt = stmt.where(Run.primary_language == language)
            if since:
                stmt = stmt.where(Run.created_at >= since)
            total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            rows = list(s.scalars(
                stmt.order_by(Run.created_at.desc()).limit(lim).offset(off)
            ))
            return rows, int(total)

    def get_run(self, run_id: str) -> Run | None:
        with self.read_session() as s:
            return s.get(Run, run_id)

    def run_counts(self, run_id: str) -> dict[str, int]:
        """Funnel-status histogram for a run, plus ``total``."""
        with self.read_session() as s:
            rows = s.execute(
                select(FindingRow.status, func.count())
                .where(FindingRow.run_id == run_id)
                .group_by(FindingRow.status)
            ).all()
        counts = {status: int(n) for status, n in rows}
        counts["total"] = sum(counts.values())
        return counts

    def query_findings(
        self,
        *,
        run_id: str | None = None,
        status: list[str] | None = None,
        severity: list[str] | None = None,
        attack_class: str | None = None,
        stable_key: str | None = None,
        order: str = "severity",
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[FindingRow], int]:
        """Filterable finding query. ``severity`` is read out of the JSON payload;
        ``attack_class`` is recovered from ``hunter_prompt_version`` (``<class>@<ver>``,
        set by the Hunt node — same convention as ``coverage.actionable_mechanical_failures``)."""
        lim, off = _clamp(limit, offset)
        with self.read_session() as s:
            stmt = select(FindingRow)
            if run_id:
                stmt = stmt.where(FindingRow.run_id == run_id)
            if status:
                stmt = stmt.where(FindingRow.status.in_(status))
            if severity:
                stmt = stmt.where(
                    func.lower(func.json_extract(FindingRow.payload, "$.severity")).in_(
                        [v.lower() for v in severity]
                    )
                )
            if attack_class:
                stmt = stmt.where(
                    (FindingRow.hunter_prompt_version == attack_class)
                    | (FindingRow.hunter_prompt_version.like(f"{attack_class}@%"))
                )
            if stable_key:
                stmt = stmt.where(FindingRow.stable_key == stable_key)
            total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0

            if order == "created_at":
                stmt = stmt.order_by(FindingRow.created_at.desc(), FindingRow.finding_id)
            else:  # "severity" — critical..low then id, matching report._sorted
                sev = func.lower(func.json_extract(FindingRow.payload, "$.severity"))
                rank = case(
                    {"critical": 0, "high": 1, "medium": 2, "low": 3},
                    value=sev,
                    else_=4,
                )
                stmt = stmt.order_by(rank, FindingRow.finding_id)
            rows = list(s.scalars(stmt.limit(lim).offset(off)))
            return rows, int(total)

    def list_validations(self, finding_id: str) -> list[ValidationRow]:
        with self.read_session() as s:
            return list(s.scalars(
                select(ValidationRow)
                .where(ValidationRow.finding_id == finding_id)
                .order_by(ValidationRow.created_at, ValidationRow.id)
            ))

    def list_wishes(
        self,
        *,
        run_id: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[WishRow], int]:
        lim, off = _clamp(limit, offset)
        with self.read_session() as s:
            stmt = select(WishRow)
            if run_id:
                stmt = stmt.where(WishRow.run_id == run_id)
            if status:
                stmt = stmt.where(WishRow.status == status)
            if since:
                stmt = stmt.where(WishRow.created_at >= since)
            total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            rows = list(s.scalars(
                stmt.order_by(WishRow.created_at.desc()).limit(lim).offset(off)
            ))
            return rows, int(total)

    def get_wish(self, wish_id: int) -> WishRow | None:
        with self.read_session() as s:
            return s.get(WishRow, wish_id)

    def set_wish_status(self, wish_id: int, status: str) -> bool:
        with self.session() as s:
            w = s.get(WishRow, wish_id)
            if w is None:
                return False
            w.status = status
            return True

    def tool_usage_by_tool(self, run_id: str) -> dict[str, dict]:
        """``{tool: {count, errors, latency_s}}`` aggregate — mirrors report._metrics."""
        out: dict[str, dict] = {}
        for u in self.tool_usage(run_id):
            t = out.setdefault(u.tool_name, {"count": 0, "errors": 0, "latency_s": 0.0})
            t["count"] += u.count
            t["errors"] += u.errors
            t["latency_s"] += float(u.latency_s or 0)
        return out


def stable_key(file_path: str, function: str, boundary: str) -> str:
    """Cross-run identity: reopen existing records rather than spawning new
    ones (§11 Dedup). Structured inputs only — not the free-text description.
    """
    raw = f"{file_path}::{function}::{boundary}".encode()
    return hashlib.sha1(raw).hexdigest()[:16]
