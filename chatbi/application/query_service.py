"""End-to-end natural-language query orchestration."""

from __future__ import annotations

import logging
from time import perf_counter

from chatbi.application.ports import (
    DatabasePort, LLMPort, QueryIntentPort, SQLSecurityPort, SchemaProviderPort,
    SimpleSemanticPort,
)
from chatbi.application.result_localizer import localize_columns
from chatbi.application.plan_execute_service import PlanAndExecuteService
from chatbi.domain.complex_analysis import StepStatus
from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.domain.models import QueryIntent, QueryResult, SemanticDiagnostics
from chatbi.domain.security import UserContext
from chatbi.logging import request_context
from chatbi.settings import Settings, get_settings
from database import QueryExecutionError
from indicator_knowledge import IndicatorKnowledge
from prompt_builder import build_prompt


logger = logging.getLogger("chatbi.query")


class ChatBIQueryService:
    def __init__(
        self,
        *,
        llm: LLMPort,
        schema_provider: SchemaProviderPort,
        security: SQLSecurityPort,
        database: DatabasePort,
        settings: Settings | None = None,
        indicators: IndicatorKnowledge | None = None,
        intent_classifier: QueryIntentPort | None = None,
        simple_semantic: SimpleSemanticPort | None = None,
        user_context: UserContext | None = None,
        plan_execute: PlanAndExecuteService | None = None,
    ) -> None:
        self.llm = llm
        self.schema_provider = schema_provider
        self.security = security
        self.database = database
        self.settings = settings or get_settings()
        self.indicators = indicators or IndicatorKnowledge()
        self.intent_classifier = intent_classifier
        self.simple_semantic = simple_semantic
        self.user_context = user_context or UserContext.demo_admin()
        self.plan_execute = plan_execute

    def query(self, question: str, *, request_id: str | None = None) -> QueryResult:
        with request_context(request_id) as resolved_request_id:
            normalized = self._validate_question(question)
            timings: dict[str, float] = {}
            total_started = perf_counter()
            try:
                started = perf_counter()
                intent = (
                    self.intent_classifier.classify(normalized)
                    if self.intent_classifier is not None
                    else QueryIntent("simple", 1.0, "意图路由未启用", "disabled")
                )
                timings["intent"] = self._elapsed(started)

                semantic_diagnostics = SemanticDiagnostics()
                if intent.query_type == "simple" and self.simple_semantic is not None:
                    started = perf_counter()
                    try:
                        semantic = self.simple_semantic.resolve(normalized)
                        knowledge = semantic.indicator_text
                        schema = semantic.schema
                        semantic_diagnostics = SemanticDiagnostics(
                            mode=semantic.mode,
                            indicators=semantic.indicators,
                            tables=schema.tables,
                            fields=semantic.fields,
                            joins=semantic.joins,
                        )
                    except Exception as exc:
                        logger.warning("simple_semantic_fallback reason=%s", type(exc).__name__)
                        knowledge = self.indicators.build_knowledge_block(normalized)
                        schema = self.schema_provider.select(normalized)
                        semantic_diagnostics = SemanticDiagnostics(mode="fallback")
                    timings["semantic"] = self._elapsed(started)
                    timings["indicator"] = 0.0
                    timings["schema"] = 0.0
                else:
                    started = perf_counter()
                    knowledge = self.indicators.build_knowledge_block(normalized)
                    timings["indicator"] = self._elapsed(started)

                    started = perf_counter()
                    schema = self.schema_provider.select(normalized)
                    timings["schema"] = self._elapsed(started)

                if (
                    intent.query_type == "complex"
                    and self.settings.feature_complex_plan_execute
                    and self.plan_execute is not None
                ):
                    outcome = self.plan_execute.run(
                        question=normalized,
                        indicator_knowledge=knowledge,
                        schema_text=schema.text,
                        detected_indicators=self.indicators.detect_indicators(normalized),
                        request_id=resolved_request_id,
                    )
                    timings["agents"] = outcome.timings_ms.get("total", 0.0)
                    timings["total"] = self._elapsed(total_started)
                    successful = [
                        item for item in outcome.step_results
                        if item.status is StepStatus.SUCCEEDED
                    ]
                    final = successful[-1]
                    return QueryResult(
                        request_id=resolved_request_id,
                        question=normalized,
                        sql=final.sql or "",
                        columns=tuple(final.columns),
                        rows=tuple(tuple(row) for row in final.rows),
                        row_count=final.row_count,
                        truncated=final.truncated,
                        schema_mode=schema.mode,
                        query_type=intent.query_type,
                        intent_confidence=intent.confidence,
                        intent_reason=intent.reason,
                        intent_source=intent.source,
                        semantic=semantic_diagnostics,
                        pipeline_stages=(
                            "intent_recognition", "complex_planning_agent",
                            "complex_execution_agent", "sql_ast_safety",
                            "connection_pool_execution", "complex_summary_agent",
                            "evidence_validation", "deterministic_report_rendering",
                        ),
                        schema_reason=schema.reason,
                        timings_ms=timings,
                        agent_mode="plan_execute",
                        agent_status=outcome.status,
                        plan=outcome.plan.model_dump(mode="json"),
                        step_results=tuple(
                            item.model_dump(mode="json") for item in outcome.step_results
                        ),
                        analysis_report=outcome.report.model_dump(mode="json", exclude={"markdown"}),
                        summary=outcome.report.executive_summary,
                        report=outcome.report.markdown,
                        report_markdown=outcome.report.markdown,
                        report_source=outcome.report.source,
                        agent_timings_ms=outcome.timings_ms,
                    )

                started = perf_counter()
                system_msg, prompt = build_prompt(
                    normalized,
                    use_few_shot=self.settings.feature_few_shot,
                    use_rules=self.settings.feature_rules,
                    use_guards=self.settings.feature_guards,
                    indicator_knowledge=(knowledge if self.settings.feature_indicator_knowledge else ""),
                    schema_text=schema.text,
                    query_type=intent.query_type,
                )
                timings["prompt"] = self._elapsed(started)

                started = perf_counter()
                generated_sql = self.llm.generate_sql(system_msg, prompt)
                timings["model"] = self._elapsed(started)

                started = perf_counter()
                secured = self.security.secure(generated_sql, self.user_context)
                timings["security"] = self._elapsed(started)

                started = perf_counter()
                columns, rows = self.database.execute(secured)
                timings["database"] = self._elapsed(started)
                timings["total"] = self._elapsed(total_started)
                logger.info(
                    "query_completed query_type=%s intent_source=%s schema_mode=%s rows=%s",
                    intent.query_type,
                    intent.source,
                    schema.mode,
                    len(rows),
                )
                slow_query = bool(getattr(self.database, "last_query_info", {}).get("slow_query", False))
                pipeline_stages = (
                    "intent_recognition",
                    "indicator_retrieval",
                    "schema_linking",
                    "dynamic_prompt",
                    "sql_generation_and_recognition",
                    "sql_ast_safety",
                    "row_level_policy_applied" if secured.row_level_applied else "row_level_policy_skipped",
                    "result_limit_applied" if secured.limited else "result_limit_not_required",
                    "connection_pool_execution",
                    "slow_query_explain" if slow_query else "slow_query_explain_skipped",
                )
                return QueryResult(
                    request_id=resolved_request_id,
                    question=normalized,
                    sql=secured.sql,
                    columns=tuple(localize_columns(columns)),
                    rows=tuple(tuple(row) for row in rows),
                    row_count=len(rows),
                    truncated=secured.limited and len(rows) >= secured.max_rows,
                    schema_mode=schema.mode,
                    query_type=intent.query_type,
                    intent_confidence=intent.confidence,
                    intent_reason=intent.reason,
                    intent_source=intent.source,
                    semantic=semantic_diagnostics,
                    row_level_applied=secured.row_level_applied,
                    pipeline_stages=pipeline_stages,
                    schema_reason=schema.reason,
                    timings_ms=timings,
                )
            except ChatBIError:
                raise
            except QueryExecutionError as exc:
                code = {
                    "query_timeout": ErrorCode.DATABASE_TIMEOUT,
                    "connection_error": ErrorCode.DATABASE_UNAVAILABLE,
                }.get(exc.error_type, ErrorCode.DATABASE_QUERY_FAILED)
                raise ChatBIError(code) from exc
            except Exception as exc:
                logger.exception("query_failed")
                raise ChatBIError(ErrorCode.INTERNAL_ERROR) from exc

    def _validate_question(self, question: str) -> str:
        normalized = question.strip() if isinstance(question, str) else ""
        if not normalized:
            raise ChatBIError(ErrorCode.INPUT_INVALID)
        if len(normalized) > self.settings.query_max_length:
            raise ChatBIError(ErrorCode.INPUT_TOO_LONG)
        return normalized

    @staticmethod
    def _elapsed(started: float) -> float:
        return round((perf_counter() - started) * 1000, 2)
