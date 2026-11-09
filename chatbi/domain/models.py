"""Framework-independent application data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class QueryIntent:
    query_type: str
    confidence: float
    reason: str
    source: str


@dataclass(frozen=True, slots=True)
class SemanticDiagnostics:
    mode: str = "not_applicable"
    indicators: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    joins: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryResult:
    request_id: str
    question: str
    sql: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    truncated: bool
    schema_mode: str
    query_type: str = "simple"
    intent_confidence: float = 1.0
    intent_reason: str = ""
    intent_source: str = "disabled"
    semantic: SemanticDiagnostics = field(default_factory=SemanticDiagnostics)
    row_level_applied: bool = False
    pipeline_stages: tuple[str, ...] = ()
    schema_reason: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)
    agent_mode: str | None = None
    agent_status: str | None = None
    plan: dict[str, Any] | None = None
    step_results: tuple[dict[str, Any], ...] = ()
    analysis_report: dict[str, Any] | None = None
    summary: str | None = None
    report: str | None = None
    report_markdown: str | None = None
    report_source: str | None = None
    agent_timings_ms: dict[str, float] = field(default_factory=dict)
