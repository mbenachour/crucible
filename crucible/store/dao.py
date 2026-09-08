"""Data-access helpers over store/models.py.

Deterministic code does deterministic work (§1.8) — dedup pre-filtering,
stable cross-run keys, provenance recording all live here, not in prompts.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from crucible.store.models import (
    Base,
    FindingRow,
    Run,
    ToolUsageRow,
    ValidationRow,
    WishRow,
)


class Store:
    def __init__(self, url: str = "sqlite:///findings.sqlite"):
        self.engine = create_engine(url, future=True)
        Base.metadata.create_all(self.engine)
        self._Session = sessionmaker(self.engine, expire_on_commit=False, future=True)

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

    # --- writes ----------------------------------------------------------
    def create_run(self, run_id: str, repo_path: str, repo_commit: str, lang: str) -> None:
        with self.session() as s:
            s.add(Run(run_id=run_id, repo_path=repo_path, repo_commit=repo_commit,
                      primary_language=lang))

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


def stable_key(file_path: str, function: str, boundary: str) -> str:
    """Cross-run identity: reopen existing records rather than spawning new
    ones (§11 Dedup). Structured inputs only — not the free-text description.
    """
    raw = f"{file_path}::{function}::{boundary}".encode()
    return hashlib.sha1(raw).hexdigest()[:16]
