"""End-to-end natural-language query orchestration."""

from __future__ import annotations

import logging
import json
from dataclasses import asdict
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
from chatbi.domain.schema import SchemaSelection
from chatbi.application.failure_control import AnalysisSuspended
from chatbi.domain.security import UserContext
from chatbi.infrastructure.complex_semantic import ComplexSemanticResolver
from chatbi.logging import request_context
from chatbi.settings import Settings, get_settings
from database import QueryExecutionError
from indicator_knowledge import IndicatorKnowledge
from prompt_builder import build_prompt
from chatbi.application.progress import progress, resumed_state
from chatbi.infrastructure.agents.validator import ResultValidationAgent
from chatbi.infrastructure.intent import obvious_non_query, conversational_reply


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
        complex_semantic: ComplexSemanticResolver | None = None,
        validator: ResultValidationAgent | None = None,
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
        self.validator = validator
        self.complex_semantic = complex_semantic or ComplexSemanticResolver(
            self.settings, self.indicators, self.simple_semantic,
        )

    def query(self, question: str, *, request_id: str | None = None) -> QueryResult:
        with request_context(request_id) as resolved_request_id:
            normalized = self._validate_question(question)
            timings: dict[str, float] = {}
            total_started = perf_counter()
            resume = resumed_state()
            if resume and resume.get("kind") == "clarification":
                resume = None
            try:
                progress("intent", message="正在识别问题意图")
                started = perf_counter()
                from chatbi.application.progress import query_options
                options = query_options()
                turns = options.get("analysis_context", {}).get("turns", [])
                latest = turns[-1]["question"] if turns else normalized
                intent_input = json.dumps({"current_question": latest,
                    "analysis_context": [turn["question"] for turn in turns[:-1]]}, ensure_ascii=False) if len(turns) > 1 else latest
                intent = QueryIntent(**resume["query_context"]["intent"]) if resume else (
                    obvious_non_query(latest) or (self.intent_classifier.classify(intent_input)
                    if self.intent_classifier is not None else QueryIntent("simple", 1.0, "意图路由未启用", "disabled")))
                timings["intent"] = self._elapsed(started)
                if intent.query_type == "non_query":
                    progress("conversation_ready", message="已识别为普通对话，未进入数据分析", query_type="non_query")
                    timings["total"] = self._elapsed(total_started)
                    return QueryResult(request_id=resolved_request_id, question=latest, sql="", columns=(), rows=(),
                        row_count=0, truncated=False, schema_mode="not_applicable", query_type="non_query",
                        intent_confidence=intent.confidence, intent_reason=intent.reason, intent_source=intent.source,
                        message=conversational_reply(latest), pipeline_stages=("intent_recognition", "conversational_response"),
                        timings_ms=timings)
                if options.get("clarify_before_query") and not resume:
                    from chatbi.application.analysis_context import require_clarification
                    require_clarification(normalized)
                progress("retrieval", message="正在召回指标、表和字段", query_type=intent.query_type)

                semantic_diagnostics = SemanticDiagnostics()
                if resume:
                    context = resume["query_context"]
                    schema = SchemaSelection(**context["schema"])
                    knowledge = resume["indicator_knowledge"]
                    semantic_diagnostics = SemanticDiagnostics(**context["semantic"])
                    timings["semantic"] = timings["indicator"] = timings["schema"] = 0.0
                elif intent.query_type == "complex":
                    started = perf_counter()
                    semantic = self.complex_semantic.resolve(normalized)
                    knowledge, schema = semantic.indicator_text, semantic.schema
                    semantic_diagnostics = SemanticDiagnostics(
                        mode=semantic.mode, indicators=semantic.indicators, tables=schema.tables,
                        fields=semantic.fields, joins=semantic.joins,
                    )
                    timings["semantic"] = self._elapsed(started)
                    timings["indicator"] = 0.0
                    timings["schema"] = 0.0
                elif self.simple_semantic is not None:
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
                    try:
                        outcome = self.plan_execute.run(
                            question=normalized,
                            indicator_knowledge=knowledge,
                            schema_text=schema.text,
                            detected_indicators=self.indicators.detect_indicators(normalized),
                            request_id=resolved_request_id,
                            **({"resume_state": resume} if resume else {}),
                        )
                    except AnalysisSuspended as exc:
                        exc.state["query_context"] = {
                            "intent": asdict(intent), "schema": asdict(schema),
                            "semantic": asdict(semantic_diagnostics),
                        }
                        raise
                    timings["agents"] = outcome.timings_ms.get("total", 0.0)
                    timings["total"] = self._elapsed(total_started)
                    successful = [
                        item for item in outcome.step_results
                        if item.status is StepStatus.SUCCEEDED
                    ]
                    final = next(
                        (item for item in successful if item.step_id == outcome.plan.final_result_step_id),
                        successful[-1],
                    )
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
                            "intent_recognition", "indicator_retrieval", "schema_linking", "complex_planning_agent",
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
                        validation={"status": "reviewed" if all(item.validation.get("status") == "reviewed" for item in successful) else "partial_review",
                                    "steps": {item.step_id: item.validation for item in successful}},
                        retrieval_diagnostics=self._retrieval_diagnostics(),
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
                progress("sql_generation", message="正在生成只读 SQL")
                generated_sql = self.llm.generate_sql(system_msg, prompt)
                timings["model"] = self._elapsed(started)

                started = perf_counter()
                progress("sql_safety", message="正在校验 SQL 安全与权限")
                secured = self.security.secure(generated_sql, self.user_context)
                timings["security"] = self._elapsed(started)

                started = perf_counter()
                progress("database", message="正在执行数据库查询")
                columns, rows = self.database.execute(secured)
                columns, rows = self.security.mask_result(columns, rows, self.user_context)
                validation = {}
                if self.validator:
                    progress("result_validation", message="校验 Agent 正在复核业务口径与结果")
                    for review_attempt in range(2):
                        validation = self.validator.validate(question=normalized, sql=secured.sql,
                            columns=list(columns), rows=list(rows), knowledge=knowledge,
                            truncated=secured.limited and len(rows) >= secured.max_rows)
                        if validation["status"] != "rejected":
                            break
                        if review_attempt == 1:
                            raise ChatBIError(ErrorCode.MODEL_OUTPUT_INVALID, "查询结果业务校验未通过，请明确条件后重试。")
                        progress("semantic_repair", message="发现业务口径问题，正在修复 SQL")
                        repair = prompt + "\n独立校验发现以下错误，请保持原业务范围修复：" + "；".join(validation["issues"])
                        secured = self.security.secure(self.llm.generate_sql(system_msg, repair), self.user_context)
                        columns, rows = self.database.execute(secured)
                        columns, rows = self.security.mask_result(columns, rows, self.user_context)
                progress("result_ready", message="查询完成，正在整理结果", row_count=len(rows))
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
                    validation=validation,
                    retrieval_diagnostics=self._retrieval_diagnostics(),
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
        from chatbi.application.progress import query_options
        limit = 10000 if query_options().get("analysis_context", {}).get("parent_query_id") else self.settings.query_max_length
        if len(normalized) > limit:
            raise ChatBIError(ErrorCode.INPUT_TOO_LONG)
        return normalized

    def _retrieval_diagnostics(self) -> dict:
        return dict(getattr(self.simple_semantic, "diagnostics", {}) or {"encoder": "static_catalog", "reranker": "not_available"})

    @staticmethod
    def _elapsed(started: float) -> float:
        return round((perf_counter() - started) * 1000, 2)
