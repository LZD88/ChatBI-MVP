"""Controlled execution agent for validated complex-query plans."""

from __future__ import annotations

import json
from time import perf_counter

from chatbi.application.ports import DatabasePort, LLMPort, SQLSecurityPort
from chatbi.application.result_localizer import localize_columns
from chatbi.domain.complex_analysis import ComplexQueryPlan, PlanStep, StepExecutionResult, StepStatus
from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.domain.security import UserContext
from chatbi.settings import Settings
from database import QueryExecutionError


class ExecutionAgent:
    """Generates one query per step and executes it through the existing safety boundary."""

    def __init__(
        self,
        llm: LLMPort,
        security: SQLSecurityPort,
        database: DatabasePort,
        settings: Settings,
        user_context: UserContext,
    ) -> None:
        self.llm = llm
        self.security = security
        self.database = database
        self.settings = settings
        self.user_context = user_context

    def execute(
        self,
        question: str,
        plan: ComplexQueryPlan,
        schema_text: str,
        indicator_knowledge: str,
        request_id: str,
    ) -> list[StepExecutionResult]:
        del request_id  # request isolation is provided by this call's local state.
        results: list[StepExecutionResult] = []
        by_id: dict[str, StepExecutionResult] = {}
        for step in plan.steps:
            failed_dependencies = [
                item for item in step.depends_on
                if item not in by_id or by_id[item].status is not StepStatus.SUCCEEDED
            ]
            if failed_dependencies:
                result = self._skipped(step)
            else:
                result = self._run_step(
                    question, plan, step, schema_text, indicator_knowledge,
                    [item for item in step.depends_on if item in by_id],
                )
            results.append(result)
            by_id[step.step_id] = result
        return results

    def _run_step(
        self,
        question: str,
        plan: ComplexQueryPlan,
        step: PlanStep,
        schema_text: str,
        knowledge: str,
        completed_dependencies: list[str],
    ) -> StepExecutionResult:
        started = perf_counter()
        last_error: Exception | None = None
        for attempt in range(self.settings.complex_step_retry + 1):
            try:
                prompt = self._step_prompt(
                    question, plan, step, schema_text, knowledge, completed_dependencies, attempt
                )
                sql = self.llm.generate_sql(self._system_message(), prompt)
                secured = self.security.secure(sql, self.user_context)
                columns, rows = self.database.execute(secured)
                columns, rows = self.security.mask_result(columns, rows, self.user_context)
                labels = localize_columns(columns)
                info = dict(getattr(self.database, "last_query_info", {}) or {})
                duration = float(info.get("duration_ms") or self._elapsed(started))
                return StepExecutionResult(
                    step_id=step.step_id,
                    step_name=step.step_name,
                    status=StepStatus.SUCCEEDED,
                    depends_on=step.depends_on,
                    sql=secured.sql,
                    columns=labels,
                    rows=[list(row) for row in rows],
                    row_count=len(rows),
                    truncated=secured.limited and len(rows) >= secured.max_rows,
                    tables=list(secured.tables),
                    result=f"查询成功，返回 {len(rows)} 行数据",
                    result_reference=f"{step.step_id}:rows",
                    duration_ms=duration,
                    slow_query=bool(info.get("slow_query", False)),
                )
            except (ChatBIError, QueryExecutionError) as exc:
                last_error = exc
            except Exception as exc:  # keep vendor/runtime details outside the public result
                last_error = exc
                break
        return self._failed(step, last_error, started)

    @staticmethod
    def _system_message() -> str:
        return (
            "你是 ChatBI 的执行 Agent。为当前单个计划步骤生成一条 MySQL 8 只读查询。"
            "只能输出一条 SELECT 或 WITH...SELECT，不得输出解释，不得执行数据库操作。"
        )

    @staticmethod
    def _step_prompt(
        question: str,
        plan: ComplexQueryPlan,
        step: PlanStep,
        schema_text: str,
        knowledge: str,
        completed_dependencies: list[str],
        attempt: int,
    ) -> str:
        step_json = json.dumps(step.model_dump(), ensure_ascii=False)
        return f"""原始问题：{question}
总体分析目标：{plan.analysis_goal}
当前步骤：{step_json}
已成功完成的依赖步骤：{json.dumps(completed_dependencies, ensure_ascii=False)}
指标口径：
{knowledge or '无明确治理指标'}
可用 Schema：
{schema_text}

约束：只使用当前步骤 required_tables/required_fields；结果列使用中文别名；
不得把其他步骤的结果值拼接进 SQL，跨步骤数据依赖必须在本查询内用 CTE 或子查询自包含表达；
不得生成写操作、系统表访问、文件函数或多语句。当前尝试序号：{attempt + 1}。
"""

    def _skipped(self, step: PlanStep) -> StepExecutionResult:
        return StepExecutionResult(
            step_id=step.step_id,
            step_name=step.step_name,
            status=StepStatus.SKIPPED,
            depends_on=step.depends_on,
            result="上游依赖步骤未成功，本步骤已安全跳过",
            result_reference=f"{step.step_id}:skipped",
            error_code="dependency_failed",
            error_message="依赖步骤失败，未执行数据库查询",
        )

    def _failed(
        self, step: PlanStep, error: Exception | None, started: float
    ) -> StepExecutionResult:
        code = "step_failed"
        message = "步骤执行失败，未获得可用结果"
        if isinstance(error, ChatBIError):
            code = error.code.value
            message = error.public_message
        elif isinstance(error, QueryExecutionError):
            code = {
                "query_timeout": ErrorCode.DATABASE_TIMEOUT.value,
                "connection_error": ErrorCode.DATABASE_UNAVAILABLE.value,
            }.get(error.error_type, ErrorCode.DATABASE_QUERY_FAILED.value)
            message = {
                ErrorCode.DATABASE_TIMEOUT.value: "查询超时，请缩小分析范围。",
                ErrorCode.DATABASE_UNAVAILABLE.value: "数据库暂时不可用。",
            }.get(code, "查询执行失败，请调整分析条件。")
        return StepExecutionResult(
            step_id=step.step_id,
            step_name=step.step_name,
            status=StepStatus.FAILED,
            depends_on=step.depends_on,
            result="步骤执行失败",
            result_reference=f"{step.step_id}:failed",
            duration_ms=self._elapsed(started),
            error_code=code,
            error_message=message,
        )

    @staticmethod
    def _elapsed(started: float) -> float:
        return round((perf_counter() - started) * 1000, 2)
