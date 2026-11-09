"""Validated contracts for the complex-query Plan-and-Execute workflow."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CHINESE_RE = re.compile(r"[\u3400-\u9fff]")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedColumn(StrictModel):
    name: str = Field(min_length=1, max_length=40)
    role: Literal["dimension", "metric"]
    data_type: Literal["string", "integer", "decimal", "percentage", "date", "datetime"]
    description: str = Field(min_length=1, max_length=160)


class PlanStep(StrictModel):
    step_id: str = Field(pattern=r"^step_[1-9][0-9]{0,2}$")
    step_name: str = Field(min_length=1, max_length=60)
    task_type: Literal[
        "metric_query", "dimension_comparison", "trend_analysis",
        "attribution_analysis", "validation",
    ] = "metric_query"
    objective: str = Field(min_length=1, max_length=300)
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    indicators: list[str] = Field(default_factory=list, max_length=20)
    dimensions: list[str] = Field(default_factory=list, max_length=12)
    filters: list[str] = Field(default_factory=list, max_length=12)
    required_tables: list[str] = Field(min_length=1, max_length=15)
    required_fields: list[str] = Field(default_factory=list, max_length=50)
    grain: list[str] = Field(default_factory=list, max_length=8)
    execution_strategy: Literal["direct_query", "aggregate_then_join"] = "direct_query"
    join_keys: list[str] = Field(default_factory=list, max_length=8)
    expected_columns: list[ExpectedColumn] = Field(default_factory=list, max_length=20)
    success_criteria: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_step(self) -> "PlanStep":
        if self.step_id in self.depends_on:
            raise ValueError("步骤不能依赖自身")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("步骤依赖不能重复")
        if self.execution_strategy == "aggregate_then_join" and (not self.grain or not self.join_keys):
            raise ValueError("聚合后关联策略必须声明 grain 和 join_keys")
        for text in (self.step_name, self.objective, self.success_criteria):
            if not CHINESE_RE.search(text):
                raise ValueError("步骤名称、目标和成功标准必须使用中文")
            if re.search(r"\b(?:SELECT|WITH|INSERT|UPDATE|DELETE|DROP)\b", text, re.IGNORECASE):
                raise ValueError("规划文本中不允许包含 SQL")
        if any(not CHINESE_RE.search(item.name) for item in self.expected_columns):
            raise ValueError("预期输出列必须使用中文名称")
        return self


class ComplexQueryPlan(StrictModel):
    analysis_goal: str = Field(min_length=1, max_length=300)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    steps: list[PlanStep] = Field(min_length=1, max_length=8)
    final_deliverable: str = Field(min_length=1, max_length=240)
    report_sections: list[str] = Field(min_length=1, max_length=10)
    source: Literal["model", "repaired", "fallback"] = "model"

    @model_validator(mode="after")
    def validate_dag(self) -> "ComplexQueryPlan":
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step_id 必须唯一")
        known: set[str] = set()
        for step in self.steps:
            unknown = set(step.depends_on) - set(step_ids)
            if unknown:
                raise ValueError(f"{step.step_id} 引用了未知依赖：{sorted(unknown)}")
            # Require stable, replayable topological order.
            if any(dependency not in known for dependency in step.depends_on):
                raise ValueError(f"{step.step_id} 只能依赖前序步骤")
            known.add(step.step_id)
        return self


class StepStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepExecutionResult(StrictModel):
    step_id: str
    step_name: str
    status: StepStatus
    depends_on: list[str] = Field(default_factory=list)
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    truncated: bool = False
    tables: list[str] = Field(default_factory=list)
    result: str
    result_reference: str
    duration_ms: float = Field(default=0, ge=0)
    slow_query: bool = False
    error_code: str | None = None
    error_message: str | None = None


class EvidenceRef(StrictModel):
    step_id: str
    row_index: int = Field(ge=0)
    column: str = Field(min_length=1, max_length=120)


class FindingDraft(StrictModel):
    title: str = Field(min_length=1, max_length=80)
    statement: str = Field(min_length=1, max_length=500)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=6)


class RecommendationDraft(StrictModel):
    action: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=400)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=6)


class SummaryAgentOutput(StrictModel):
    title: str = Field(min_length=1, max_length=100)
    executive_summary: str = Field(min_length=1, max_length=1000)
    findings: list[FindingDraft] = Field(default_factory=list, max_length=8)
    caveats: list[str] = Field(default_factory=list, max_length=8)
    recommendations: list[RecommendationDraft] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def require_chinese(self) -> "SummaryAgentOutput":
        texts = [self.title, self.executive_summary, *self.caveats]
        texts += [item.title for item in self.findings]
        texts += [item.statement for item in self.findings]
        texts += [item.action for item in self.recommendations]
        texts += [item.rationale for item in self.recommendations]
        if any(text and not CHINESE_RE.search(text) for text in texts):
            raise ValueError("总结内容必须使用中文")
        return self


class ResolvedEvidence(StrictModel):
    step_id: str
    step_name: str
    row_index: int
    column: str
    value: Any


class AnalysisFinding(StrictModel):
    title: str
    statement: str
    evidence: list[ResolvedEvidence]


class AnalysisRecommendation(StrictModel):
    action: str
    rationale: str
    evidence: list[ResolvedEvidence]


class AnalysisReport(StrictModel):
    title: str
    executive_summary: str
    findings: list[AnalysisFinding] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    recommendations: list[AnalysisRecommendation] = Field(default_factory=list)
    source: Literal["summary_agent", "template_fallback"]
    fallback_reason: str | None = None
    markdown: str


class ComplexExecutionOutcome(StrictModel):
    plan: ComplexQueryPlan
    step_results: list[StepExecutionResult]
    report: AnalysisReport
    status: Literal["succeeded", "partial", "failed"]
    timings_ms: dict[str, float]
