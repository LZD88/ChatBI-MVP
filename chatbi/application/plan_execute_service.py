"""Application orchestrator for the three-agent Plan-and-Execute workflow."""

from __future__ import annotations

from time import perf_counter
from typing import Literal

from chatbi.domain.complex_analysis import ComplexExecutionOutcome, StepStatus
from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.infrastructure.agents.executor import ExecutionAgent
from chatbi.infrastructure.agents.planner import PlannerAgent
from chatbi.infrastructure.agents.summarizer import SummaryAgent


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
    ) -> ComplexExecutionOutcome:
        timings: dict[str, float] = {}
        total_started = perf_counter()

        started = perf_counter()
        plan = self.planner.plan(
            question, indicator_knowledge, schema_text, detected_indicators
        )
        timings["planning"] = self._elapsed(started)

        started = perf_counter()
        results = self.executor.execute(
            question, plan, schema_text, indicator_knowledge, request_id
        )
        timings["execution"] = self._elapsed(started)
        successful = [item for item in results if item.status is StepStatus.SUCCEEDED]
        if not successful:
            self._raise_all_failed(results)

        started = perf_counter()
        report = self.summarizer.summarize(question, plan, results)
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

    @staticmethod
    def _raise_all_failed(results) -> None:
        codes = {item.error_code for item in results if item.error_code}
        mapping = {
            ErrorCode.DATABASE_TIMEOUT.value: ErrorCode.DATABASE_TIMEOUT,
            ErrorCode.DATABASE_UNAVAILABLE.value: ErrorCode.DATABASE_UNAVAILABLE,
            ErrorCode.SQL_REJECTED.value: ErrorCode.SQL_REJECTED,
            ErrorCode.MODEL_UNAVAILABLE.value: ErrorCode.MODEL_UNAVAILABLE,
            ErrorCode.MODEL_OUTPUT_INVALID.value: ErrorCode.MODEL_OUTPUT_INVALID,
        }
        for name, code in mapping.items():
            if name in codes:
                raise ChatBIError(code)
        raise ChatBIError(ErrorCode.DATABASE_QUERY_FAILED)

    @staticmethod
    def _elapsed(started: float) -> float:
        return round((perf_counter() - started) * 1000, 2)
