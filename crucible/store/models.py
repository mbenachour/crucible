"""Domain store — SQLite via SQLAlchemy (specs.md §3).

Two stores, deliberately: LangGraph checkpoints hold EXECUTION state; this
store holds DOMAIN state — findings, validations, provenance, wishlist,
tool-usage counters. Findings must outlive and be queryable independently of
any run.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    repo_path: Mapped[str] = mapped_column(String)
    repo_commit: Mapped[str] = mapped_column(String)
    primary_language: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    status: Mapped[str] = mapped_column(String, default="running")

    findings: Mapped[list["FindingRow"]] = relationship(back_populates="run")


class FindingRow(Base):
    __tablename__ = "findings"

    finding_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"))
    stable_key: Mapped[str] = mapped_column(String, index=True)  # cross-run dedup key

    # Hunt output (schema.Finding as JSON, field order preserved)
    payload: Mapped[dict] = mapped_column(JSON)

    # Funnel status: raw -> mechanical_failed | mechanical_passed
    #                    -> bug_refuted | bug_upheld
    #                    -> reach_refuted | reach_upheld
    status: Mapped[str] = mapped_column(String, default="raw", index=True)

    # Provenance (§6): recorded on every finding
    hunter_model: Mapped[str] = mapped_column(String)
    hunter_prompt_version: Mapped[str] = mapped_column(String)
    hunter_sampling: Mapped[dict] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    run: Mapped[Run] = relationship(back_populates="findings")

    validations: Mapped[list["ValidationRow"]] = relationship(back_populates="finding")


class ValidationRow(Base):
    __tablename__ = "validations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.finding_id"))
    pass_name: Mapped[str] = mapped_column(String)  # mechanical | bug | reachability
    verdict: Mapped[str] = mapped_column(String)    # upheld | refuted | mechanical_failed
    reasoning: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String, default="")            # empty for mechanical
    prompt_version: Mapped[str] = mapped_column(String, default="")
    response_class: Mapped[str] = mapped_column(String, default="")   # llm/classify.py
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    finding: Mapped[FindingRow] = relationship(back_populates="validations")


class WishRow(Base):
    __tablename__ = "wishlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"))
    blocked_task_id: Mapped[str] = mapped_column(String)
    need: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="open")  # open | resolved | requeued
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ToolUsageRow(Base):
    __tablename__ = "tool_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"))
    role: Mapped[str] = mapped_column(String)
    tool_name: Mapped[str] = mapped_column(String)
    count: Mapped[int] = mapped_column(Integer, default=0)
    latency_s: Mapped[float] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
