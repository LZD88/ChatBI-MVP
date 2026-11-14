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


class InputBinding(StrictModel):
    source_step_id: str = Field(pattern=r"^step_[1-9][0-9]{0,2}$")
    name: str = Field(pattern=r"^input_[1-9][0-9]?$", description="下游 SQL 引用的只读结果表名")
    columns: list[str] = Field(min_length=1, max_length=20)


class PlanStep(StrictModel):
    step_id: str = Field(pattern=r"^step_[1-9][0-9]{0,2}$")
    step_name: str = Field(min_length=1, max_length=60)
    task_type: Literal[
        "metric_query", "dimension_comparison", "trend_analysis",
        "attribution_analysis", "validation",
    ] = "metric_query"
    objective: str = Field(min_length=1, max_length=300)
    importance: Literal["high", "low"] = Field(default="low", description="核心数据为 high；可缺省的辅助分析为 low")
    importance_reason: str = Field(default="辅助步骤；最终结果及其依赖将由系统提升为高权重", max_length=200)
    max_retries: int | None = Field(default=None, ge=0, le=3, description="额外重试次数，受系统按权重设置的上限约束")
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    input_bindings: list[InputBinding] = Field(default_factory=list, max_length=8)
    indicators: list[str] = Field(default_factory=list, max_length=20)
    dimensions: list[str] = Field(default_factory=list, max_length=12)
    filters: list[str] = Field(default_factory=list, max_length=12)
    required_tables: list[str] = Field(default_factory=list, max_length=15)
    required_fields: list[str] = Field(default_factory=list, max_length=50)
    grain: list[str] = Field(default_factory=list, max_length=8)
    execution_strategy: Literal["direct_query", "aggregate_then_join"] = "direct_query"
    join_keys: list[str] = Field(default_factory=list, max_length=8)
    expected_columns: list[ExpectedColumn] = Field(default_factory=list, max_length=20)
    success_criteria: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_step(self) -> "PlanStep":
        if not self.required_tables and not self.input_bindings:
            raise ValueError("步骤必须声明业务表或上游结果绑定")
        if len({item.name for item in self.input_bindings}) != len(self.input_bindings):
            raise ValueError("输入绑定名称不能重复")
        if any(item.source_step_id not in self.depends_on for item in self.input_bindings):
            raise ValueError("输入绑定来源必须出现在 depends_on 中")
        if any(len(set(item.columns)) != len(item.columns) for item in self.input_bindings):
            raise ValueError("输入绑定字段不能重复")
        if len({item.name for item in self.expected_columns}) != len(self.expected_columns):
            raise ValueError("输出契约列名不能重复")
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


class ReconciliationCheck(StrictModel):
    total_step_id: str
    detail_step_id: str
    total_column: str
    detail_column: str
    tolerance: float = Field(default=0.02, ge=0, le=1)


class ComplexQueryPlan(StrictModel):
    analysis_goal: str = Field(min_length=1, max_length=300)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    steps: list[PlanStep] = Field(min_length=1, max_length=8)
    final_result_step_id: str | None = Field(
        default=None, description="用户所需最终数据表对应的步骤 ID；报告及建议由总结 Agent 生成"
    )
    final_deliverable: str = Field(min_length=1, max_length=240)
    report_sections: list[str] = Field(min_length=1, max_length=10)
    source: Literal["model", "repaired", "fallback"] = "model"
    reconciliation_checks: list[ReconciliationCheck] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_dag(self) -> "ComplexQueryPlan":
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step_id 必须唯一")
        if self.final_result_step_id is None:
            self.final_result_step_id = step_ids[-1]
        if self.final_result_step_id not in step_ids:
            raise ValueError("最终数据结果必须引用计划中的步骤")
        known: set[str] = set()
        by_id = {step.step_id: step for step in self.steps}
        for check in self.reconciliation_checks:
            if check.total_step_id == check.detail_step_id:
                raise ValueError("总分校验必须引用不同步骤")
            for step_id, column in ((check.total_step_id, check.total_column), (check.detail_step_id, check.detail_column)):
                if step_id not in by_id or not any(item.name == column and item.role == "metric" for item in by_id[step_id].expected_columns):
                    raise ValueError("总分校验必须引用已有数值指标列")
                if any(word in column for word in ("率", "平均", "比例", "占比")):
                    raise ValueError("比例和平均指标不可直接进行加总校验")
        for step in self.steps:
            unknown = set(step.depends_on) - set(step_ids)
            if unknown:
                raise ValueError(f"{step.step_id} 引用了未知依赖：{sorted(unknown)}")
            # Require stable, replayable topological order.
            if any(dependency not in known for dependency in step.depends_on):
                raise ValueError(f"{step.step_id} 只能依赖前序步骤")
            for binding in step.input_bindings:
                source_columns = {item.name for item in by_id[binding.source_step_id].expected_columns}
                if set(binding.columns) - source_columns:
                    raise ValueError("输入绑定字段必须在上游 expected_columns 输出契约中声明")
            known.add(step.step_id)
        # Final output and its entire dependency closure cannot be downgraded
        # by model labels. Explicit additional high-weight steps remain high.
        core = {self.final_result_step_id}
        for step in reversed(self.steps):
            if step.step_id in core or step.importance == "high":
                core.add(step.step_id)
                core.update(step.depends_on)
        for step in self.steps:
            if step.step_id in core:
                step.importance = "high"
                step.importance_reason = "核心结果步骤或高权重步骤的前置依赖"
        return self


class StepStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class ResultArtifact(StrictModel):
    artifact_id: str
    columns: list[ExpectedColumn]
    row_count: int = Field(ge=0)
    complete: bool


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
    result_artifact: ResultArtifact | None = None
    consumed_artifacts: list[str] = Field(default_factory=list)
    schema_mode: str | None = None
    schema_tables: list[str] = Field(default_factory=list)
    importance: Literal["high", "low"] = "low"
    attempts: int = Field(default=0, ge=0)
    recovery_action: Literal["none", "repaired", "soft_skip", "dependency_skip", "suspend", "circuit_blocked", "reused"] = "none"
    attempt_errors: list[dict[str, Any]] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)


class EvidenceRef(StrictModel):
    step_id: str
    row_index: int = Field(ge=0)
    column: str = Field(min_length=1, max_length=120)


class FindingDraft(StrictModel):
    title: str = Field(min_length=1, max_length=80)
    statement: str = Field(min_length=1, max_length=500)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=6)


class ActionDetails(StrictModel):
    target: str = Field(min_length=1, max_length=120)
    problem: str = Field(min_length=1, max_length=300)
    owner_role: str = Field(min_length=1, max_length=80)
    priority: Literal["high", "medium", "low"]
    expected_impact: str = Field(min_length=1, max_length=240)
    precondition: str = Field(min_length=1, max_length=240)
    risk: str = Field(min_length=1, max_length=240)
    confidence: Literal["high", "medium", "low"]


class RecommendationDraft(ActionDetails):
    action: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=400)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=6)
    target_evidence: EvidenceRef = Field(description="target 对应的原始结果单元格，须同时出现在 evidence 中")


class SummaryAgentOutput(StrictModel):
    title: str = Field(min_length=1, max_length=100)
    executive_summary: str = Field(min_length=1, max_length=1000)
    findings: list[FindingDraft] = Field(default_factory=list, max_length=8)
    caveats: list[str] = Field(default_factory=list, max_length=8)
    recommendation_status: Literal["actionable", "insufficient_data"]
    recommendation_reason: str = Field(min_length=1, max_length=300)
    recommendations: list[RecommendationDraft] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def require_chinese(self) -> "SummaryAgentOutput":
        texts = [self.title, self.executive_summary, *self.caveats]
        texts += [item.title for item in self.findings]
        texts += [item.statement for item in self.findings]
        texts += [item.action for item in self.recommendations]
        texts += [item.rationale for item in self.recommendations]
        texts += [self.recommendation_reason]
        for item in self.recommendations:
            texts += [item.problem, item.owner_role, item.expected_impact, item.precondition, item.risk]
        if any(text and not CHINESE_RE.search(text) for text in texts):
            raise ValueError("总结内容必须使用中文")
        if (self.recommendation_status == "actionable") != bool(self.recommendations):
            raise ValueError("可行动状态必须包含建议；数据不足时禁止填充泛化建议")
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


class AnalysisRecommendation(ActionDetails):
    action: str
    rationale: str
    evidence: list[ResolvedEvidence]


class AnalysisReport(StrictModel):
    title: str
    executive_summary: str
    findings: list[AnalysisFinding] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    recommendations: list[AnalysisRecommendation] = Field(default_factory=list)
    recommendation_status: Literal["actionable", "insufficient_data"] = "insufficient_data"
    recommendation_reason: str = "当前数据不足以支持具体业务动作"
    source: Literal["summary_agent", "template_fallback"]
    fallback_reason: str | None = None
    markdown: str


class ComplexExecutionOutcome(StrictModel):
    plan: ComplexQueryPlan
    step_results: list[StepExecutionResult]
    report: AnalysisReport
    status: Literal["succeeded", "partial", "failed"]
    timings_ms: dict[str, float]
