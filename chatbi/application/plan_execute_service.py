"""Application orchestrator for the three-agent Plan-and-Execute workflow."""

from __future__ import annotations

from time import perf_counter
from typing import Literal

from chatbi.domain.complex_analysis import ComplexExecutionOutcome, ComplexQueryPlan, StepExecutionResult, StepStatus
from typing import Any
from chatbi.application.failure_control import AnalysisSuspended
from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.infrastructure.agents.executor import ExecutionAgent
from chatbi.infrastructure.agents.planner import PlannerAgent
from chatbi.infrastructure.agents.summarizer import SummaryAgent
from chatbi.application.progress import progress, query_options


class PlanAndExecuteService:
    def __init__(
        self, planner: PlannerAgent, executor: ExecutionAgent, summarizer: SummaryAgent
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.summarizer = summarizer

    def run(
        self,
        *,
        question: str,
        indicator_knowledge: str,
        schema_text: str,
        detected_indicators: list[str],
        request_id: str,
        resume_state: dict[str, Any] | None = None,
    ) -> ComplexExecutionOutcome:
        timings: dict[str, float] = {}
        total_started = perf_counter()

        started = perf_counter()
        progress("planning", message="规划 Agent 正在拆解分析任务")
        plan = (ComplexQueryPlan.model_validate(resume_state["plan"]) if resume_state else
                self.planner.plan(question, indicator_knowledge, schema_text, detected_indicators))
        timings["planning"] = self._elapsed(started)
        if resume_state and resume_state.get("replan_requested"):
            plan, resume_state = self._replan(plan, resume_state, schema_text, indicator_knowledge)
        progress("plan_ready", message="规划已完成", step_count=len(plan.steps), source=plan.source)

        if query_options().get("review_plan") and not resume_state:
            state = {"kind": "plan_review", "version": 1, "question": question, "plan": plan.model_dump(),
                     "schema_text": schema_text, "indicator_knowledge": indicator_knowledge,
                     "detected_indicators": detected_indicators, "cached_results": [],
                     "resume_round": 0, "max_resume_rounds": self.executor.settings.complex_max_resume_rounds}
            raise AnalysisSuspended(state, {"kind": "plan_review", "message": "计划已生成，确认或修改未执行步骤后继续。",
                "plan": plan.model_dump(mode="json"), "step_results": [], "options": ["retry", "abort"],
                "resume_round": 0, "max_resume_rounds": state["max_resume_rounds"]})

        started = perf_counter()
        if resume_state:
            cached = [StepExecutionResult.model_validate(item) for item in resume_state["cached_results"]]
            results = self.executor.execute(
                question, plan, schema_text, indicator_knowledge, request_id,
                cached_results=cached, clarification=resume_state.get("clarification", ""),
            )
        else:
            results = self.executor.execute(question, plan, schema_text, indicator_knowledge, request_id)
        timings["execution"] = self._elapsed(started)
        failed_core = next((item for item in results if item.recovery_action == "suspend"), None)
        replan_count = resume_state.get("replan_count", 0) if resume_state else 0
        if (failed_core and replan_count < 2 and failed_core.attempt_errors
                and failed_core.attempt_errors[-1].get("retryable")):
            failed_index = next(i for i, item in enumerate(results) if item.step_id == failed_core.step_id)
            local_state = {"plan": plan.model_dump(), "cached_results": [item.model_dump() for item in results[:failed_index]],
                           "failed_step_id": failed_core.step_id, "failure_reason": failed_core.attempt_errors[-1],
                           "detected_indicators": detected_indicators, "replan_count": replan_count}
            try:
                plan, adjusted = self._replan(plan, local_state, schema_text, indicator_knowledge)
                results = self.executor.execute(question, plan, schema_text, indicator_knowledge, request_id,
                    cached_results=[StepExecutionResult.model_validate(row) for row in adjusted["cached_results"]])
                failed_core = next((item for item in results if item.recovery_action == "suspend"), None)
            except Exception:
                progress("replan_rejected", message="局部重规划未通过契约校验，保留原计划和有效结果")
            replan_count += 1
        if failed_core:
            failed_index = next(i for i, item in enumerate(results) if item.step_id == failed_core.step_id)
            rounds = resume_state.get("resume_round", 0) if resume_state else 0
            state = {
                "version": 1, "question": question, "plan": plan.model_dump(),
                "schema_text": schema_text, "indicator_knowledge": indicator_knowledge,
                "detected_indicators": detected_indicators,
                "cached_results": [item.model_dump() for item in results[:failed_index]],
                "resume_round": rounds, "max_resume_rounds": self.executor.settings.complex_max_resume_rounds,
                "clarification": resume_state.get("clarification", "") if resume_state else "",
                "failed_step_id": failed_core.step_id,
                "failure_reason": failed_core.attempt_errors[-1] if failed_core.attempt_errors else {},
                "replan_count": replan_count,
            }
            public = {
                "message": f"核心步骤“{failed_core.step_name}”失败，后续任务已熔断，进度已保存。",
                "failed_step_id": failed_core.step_id, "failed_step_name": failed_core.step_name,
                "reason": failed_core.error_message, "error_code": failed_core.error_code,
                "plan": plan.model_dump(mode="json"),
                "step_results": [item.model_dump(mode="json") for item in results],
                "resume_round": rounds, "max_resume_rounds": self.executor.settings.complex_max_resume_rounds,
                "options": ["retry", "clarify", "replan", "abort"] if rounds < self.executor.settings.complex_max_resume_rounds else ["abort"],
                "clarification_hint": "仅影响未完成步骤；若需修改已完成结果的筛选范围或指标口径，请重新查询。不会自动替换治理指标或绕过权限。",
            }
            raise AnalysisSuspended(state, public)
        successful = [item for item in results if item.status is StepStatus.SUCCEEDED]
        if not successful:
            self._raise_all_failed(results)

        from chatbi.infrastructure.agents.validator import reconcile
        reconciliations = reconcile(plan, results)
        if any(item["status"] == "rejected" for item in reconciliations):
            state = {"kind": "failure", "version": 1, "question": question, "plan": plan.model_dump(),
                     "schema_text": schema_text, "indicator_knowledge": indicator_knowledge,
                     "detected_indicators": detected_indicators, "cached_results": [],
                     "resume_round": resume_state.get("resume_round", 0) if resume_state else 0,
                     "max_resume_rounds": self.executor.settings.complex_max_resume_rounds, "replan_count": replan_count}
            raise AnalysisSuspended(state, {"kind": "failure", "message": "总计与明细加总不一致，已停止报告生成，请复核口径后重试。",
                "reason": "同口径总分校验失败", "reconciliations": reconciliations, "plan": plan.model_dump(mode="json"),
                "step_results": [item.model_dump(mode="json") for item in results], "options": ["retry", "abort"]})
        if reconciliations:
            results[-1].validation["reconciliations"] = reconciliations
            if any(item["status"] == "unverified" for item in reconciliations):
                results[-1].validation.setdefault("limitations", []).append("总分校验未完成：总计/明细缺失、被截断或不可加总，不能认定一致。")

        started = perf_counter()
        progress("summarizing", message="总结 Agent 正在核对证据并生成报告")
        report = self.summarizer.summarize(question, plan, results)
        progress("report_ready", message="分析报告已生成", source=report.source)
        timings["summary"] = self._elapsed(started)
        timings["total"] = self._elapsed(total_started)
        status: Literal["succeeded", "partial", "failed"] = (
            "succeeded" if len(successful) == len(results) else "partial"
        )
        return ComplexExecutionOutcome(
            plan=plan,
            step_results=results,
            report=report,
            status=status,
            timings_ms=timings,
        )

    def _replan(self, plan: ComplexQueryPlan, state: dict[str, Any], schema: str, knowledge: str):
        from chatbi.application.analysis_control import affected_steps
        from chatbi.infrastructure.agents.planner import extract_json_object
        import json
        if state.get("replan_count", 0) >= 2:
            raise ChatBIError(ErrorCode.MODEL_OUTPUT_INVALID, "局部重规划最多两次，请调整问题后重试。")
        affected = affected_steps(plan, {state["failed_step_id"]})
        allowed = [step.model_dump() for step in plan.steps if step.step_id in affected]
        progress("replanning", message="正在调整失败节点和下游计划，保留其他成功结果")
        raw = self.planner.llm.generate_text(
            "你是局部重规划 Agent。只返回 JSON 对象，steps 包含所给节点的完整修订。"
            "保持节点 ID、依赖、输入绑定、指标、维度、筛选与输出契约不变。"
            "只修改 objective、execution_strategy、grain、join_keys、required_tables、required_fields。"
            "保持业务范围和治理口径，通过分步聚合或调整关联修复问题。禁止 SQL。",
            json.dumps({"steps": allowed, "failure": state.get("failure_reason"), "schema": schema,
                        "metrics": knowledge}, ensure_ascii=False, default=str))
        draft = json.loads(extract_json_object(raw))
        replacements = {step["step_id"]: step for step in draft["steps"]}
        if set(replacements) != affected or len(draft["steps"]) != len(affected):
            raise ValueError("重规划必须仅覆盖失败节点及其下游。")
        editable = {"objective", "execution_strategy", "grain", "join_keys", "required_tables", "required_fields"}
        data = plan.model_dump()
        for i, step in enumerate(data["steps"]):
            if step["step_id"] not in affected:
                continue
            replacement = replacements[step["step_id"]]
            if any(replacement.get(key) != value for key, value in step.items() if key not in editable):
                raise ValueError("重规划试图修改业务范围或输出契约。")
            data["steps"][i] = replacement
        updated = ComplexQueryPlan.model_validate(data)
        self.planner._validate_catalog(updated, state["detected_indicators"], schema)
        state = {**state, "plan": updated.model_dump(), "replan_requested": False,
                 "replan_count": state.get("replan_count", 0) + 1,
                 "cached_results": [row for row in state["cached_results"] if row["step_id"] not in affected]}
        progress("replanned", affected_steps=sorted(affected), message="局部重规划校验通过")
        return updated, state

    @staticmethod
    def _raise_all_failed(results) -> None:
        codes = {item.error_code for item in results if item.error_code}
        mapping = {
            ErrorCode.DATABASE_TIMEOUT.value: ErrorCode.DATABASE_TIMEOUT,
            ErrorCode.DATABASE_UNAVAILABLE.value: ErrorCode.DATABASE_UNAVAILABLE,
            ErrorCode.SQL_REJECTED.value: ErrorCode.SQL_REJECTED,
            ErrorCode.MODEL_UNAVAILABLE.value: ErrorCode.MODEL_UNAVAILABLE,
            ErrorCode.MODEL_OUTPUT_INVALID.value: ErrorCode.MODEL_OUTPUT_INVALID,
            "step_contract_invalid": ErrorCode.MODEL_OUTPUT_INVALID,
        }
        for name, code in mapping.items():
            if name in codes:
                raise ChatBIError(code)
        raise ChatBIError(ErrorCode.DATABASE_QUERY_FAILED)

    @staticmethod
    def _elapsed(started: float) -> float:
        return round((perf_counter() - started) * 1000, 2)
