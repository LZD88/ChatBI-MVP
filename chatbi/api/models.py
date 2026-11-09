"""Public HTTP request and response contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10000)


class QueryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: str
    question: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    schema_mode: str
    query_type: str
    intent_confidence: float
    intent_reason: str
    intent_source: str
    semantic_mode: str
    recalled_indicators: list[str]
    recalled_tables: list[str]
    recalled_fields: list[str]
    inferred_joins: list[str]
    row_level_applied: bool
    pipeline_stages: list[str]
    schema_reason: str | None = None
    timings_ms: dict[str, float]
    agent_mode: str | None = None
    agent_status: str | None = None
    plan: dict[str, Any] | None = None
    step_results: list[dict[str, Any]] = Field(default_factory=list)
    analysis_report: dict[str, Any] | None = None
    summary: str | None = None
    report: str | None = None
    report_markdown: str | None = None
    report_source: str | None = None
    agent_timings_ms: dict[str, float] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    request_id: str
    code: str
    message: str
    fields: list[dict[str, Any]] | None = None


class ComponentHealth(BaseModel):
    status: str
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    components: dict[str, ComponentHealth] = Field(default_factory=dict)
