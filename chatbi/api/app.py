"""FastAPI application factory and local server entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import Any, Callable
from threading import RLock
from urllib.parse import urlparse
import asyncio
import json
import hashlib
import re
from queue import Full
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from chatbi.api.models import ComponentHealth, ErrorResponse, HealthResponse, QueryRequest, QueryResponse
from chatbi.application.query_service import ChatBIQueryService
from chatbi.application.plan_execute_service import PlanAndExecuteService
from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.domain.security import QuerySecurityManager, UserContext
from chatbi.infrastructure.schema import (
    FallbackSchemaProvider,
    SemanticSchemaProvider,
    StaticSchemaProvider,
    indexes_ready,
)
from chatbi.infrastructure.intent import DisabledIntentClassifier, FallbackIntentClassifier, QwenIntentClassifier
from chatbi.logging import configure_logging, new_request_id
from chatbi.settings import Settings, get_settings
from database import DatabaseClient
from llm_client import LLMClient
from indicator_knowledge import IndicatorKnowledge
from chatbi.infrastructure.agents.executor import ExecutionAgent
from chatbi.infrastructure.agents.planner import PlannerAgent
from chatbi.infrastructure.agents.summarizer import SummaryAgent
from chatbi.infrastructure.complex_semantic import ComplexSemanticResolver
from chatbi.infrastructure.metric_registry import CatalogDecision, CatalogError, MetricDraft, MetricRegistry
from chatbi.application.visualization import build_visualizations
from chatbi.application.query_jobs import QueryJobs
from chatbi.application.progress import checkpoint, resumed_state
from chatbi.application.progress import query_options, analysis_options
from chatbi.application.analysis_context import conversation_context, effective_question, require_clarification, apply_answers
from chatbi.application.analysis_control import preview_edits
from chatbi.infrastructure.agents.validator import ResultValidationAgent
from chatbi.application.failure_control import AnalysisSuspended
from chatbi.infrastructure.query_history import QueryHistory, TERMINAL, ResumeConflict
from chatbi.infrastructure.runtime_version import source_version
from pydantic import BaseModel, Field
from typing import Literal


class HistoryAnnotation(BaseModel):
    favorite: bool | None = None
    feedback: Literal["useful", "incorrect", "needs_data"] | None = None
    feedback_note: str | None = Field(default=None, max_length=1000)


class ResumeRequest(BaseModel):
    action: Literal["retry", "clarify", "replan", "edit", "abort"] = "retry"
    clarification: str = Field(default="", max_length=1000)
    answers: dict[str, str] = Field(default_factory=dict)
    edits: dict[str, list[str]] = Field(default_factory=dict)
    preview_token: str = Field(default="", max_length=64)


class PlanEditRequest(BaseModel):
    edits: dict[str, list[str]]


class DrilldownRequest(BaseModel):
    step_id: str | None = Field(default=None, pattern=r"^step_[1-9][0-9]{0,2}$")
    dimension: str = Field(min_length=1, max_length=120)
    value: str | int | float | None = None


ERROR_STATUS = {
    ErrorCode.INPUT_INVALID: 422,
    ErrorCode.INPUT_TOO_LONG: 422,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.MODEL_OUTPUT_INVALID: 502,
    ErrorCode.SCHEMA_UNAVAILABLE: 503,
    ErrorCode.SQL_REJECTED: 422,
    ErrorCode.DATABASE_UNAVAILABLE: 503,
    ErrorCode.DATABASE_TIMEOUT: 504,
    ErrorCode.DATABASE_QUERY_FAILED: 422,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.ANALYSIS_SUSPENDED: 409,
}

logger = logging.getLogger("chatbi.api")


def refresh_query_catalog(service: Any, knowledge: IndicatorKnowledge) -> None:
    """Swap a request snapshot into all consumers before execution under one lock."""
    if not isinstance(service, ChatBIQueryService):
        return
    service.indicators = knowledge
    service.complex_semantic.indicators = knowledge
    if service.simple_semantic is not None and hasattr(service.simple_semantic, "update_knowledge"):
        service.simple_semantic.update_knowledge(knowledge)
    if service.plan_execute is not None:
        service.plan_execute.planner.indicators = knowledge


def build_query_service(settings: Settings) -> ChatBIQueryService:
    settings.validate_query_runtime()
    _, indicators = MetricRegistry(settings.metric_registry_path).snapshot()
    schema_provider = FallbackSchemaProvider(SemanticSchemaProvider(), StaticSchemaProvider())
    security = QuerySecurityManager(
        schema={table: fields for table, fields in QuerySecurityManager().schema.items() if table in settings.allowed_tables},
        max_rows=settings.query_max_rows,
        sensitive_fields=settings.sensitive_fields,
    )
    database = DatabaseClient(query_timeout_seconds=settings.query_timeout_seconds)
    intent_classifier = (
        FallbackIntentClassifier(QwenIntentClassifier(settings))
        if settings.feature_intent_router
        else DisabledIntentClassifier()
    )
    simple_semantic = None
    if settings.feature_simple_chroma:
        try:
            from chatbi.infrastructure.simple_semantic import ChromaSimpleSemanticPipeline
            simple_semantic = ChromaSimpleSemanticPipeline(settings, indicators)
        except Exception as exc:
            # Chroma is an optional retrieval accelerator. A stopped VM must
            # not take the query API down; the query service retains its
            # governed static/legacy Schema path.
            logger.warning("simple_semantic_startup_fallback reason=%s", type(exc).__name__)
    llm = LLMClient(settings)
    complex_semantic = ComplexSemanticResolver(settings, indicators, simple_semantic)
    user_context = UserContext.demo_admin()
    plan_execute = None
    if settings.feature_complex_plan_execute:
        plan_execute = PlanAndExecuteService(
            PlannerAgent(llm, settings, indicators),
            ExecutionAgent(llm, security, database, settings, user_context, complex_semantic, ResultValidationAgent(llm)),
            SummaryAgent(llm, settings),
        )
    return ChatBIQueryService(
        llm=llm,
        schema_provider=schema_provider,
        security=security,
        database=database,
        settings=settings,
        intent_classifier=intent_classifier,
        simple_semantic=simple_semantic,
        indicators=indicators,
        user_context=user_context,
        plan_execute=plan_execute,
        complex_semantic=complex_semantic,
        validator=ResultValidationAgent(llm),
    )


def create_app(
    settings: Settings | None = None,
    *,
    service_factory: Callable[[Settings], Any] = build_query_service,
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = resolved
        app.state.query_service = None
        app.state.startup_error = None
        app.state.catalog_lock = RLock()
        app.state.catalog = MetricRegistry(resolved.metric_registry_path)
        app.state.catalog_revision = None
        app.state.source_version = source_version()
        app.state.history = QueryHistory(resolved.query_history_path, resolved.history_retention)
        try:
            app.state.query_service = service_factory(resolved)
        except Exception as exc:
            app.state.startup_error = str(exc)
        app.state.jobs = QueryJobs(app.state.history, lambda question, query_id: execute_query(question, query_id).model_dump(mode="json"),
                                   capacity=resolved.query_job_capacity, timeout=resolved.query_total_timeout_seconds)
        yield
        stopped = app.state.jobs.close()
        service = app.state.query_service
        database = getattr(service, "database", None)
        if stopped and database is not None and hasattr(database, "close"):
            database.close()

    app = FastAPI(
        title="ChatBI MVP API",
        version="1.0.0",
        description="安全的本地自然语言转 MySQL 查询 API",
        lifespan=lifespan,
    )

    @app.exception_handler(ChatBIError)
    async def domain_error_handler(request: Request, exc: ChatBIError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", new_request_id())
        body = ErrorResponse(request_id=request_id, code=exc.code.value, message=exc.public_message)
        if isinstance(exc, AnalysisSuspended):
            body.suspension = exc.public
            body.history_id = exc.details.get("history_id")
        return JSONResponse(status_code=ERROR_STATUS[exc.code], content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", new_request_id())
        fields = [
            {key: value for key, value in error.items() if key not in {"input", "ctx", "url"}}
            for error in exc.errors()
        ]
        body = ErrorResponse(
            request_id=request_id,
            code=ErrorCode.INPUT_INVALID.value,
            message="请求格式不正确，请检查问题字段。",
            fields=fields,
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or new_request_id()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    error_responses: dict[int | str, dict[str, Any]] = {
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    }

    def policy_fingerprint() -> str:
        service = app.state.query_service
        value = repr((getattr(service, "user_context", None), resolved.query_allowed_tables,
                      resolved.query_sensitive_fields, resolved.db_host, resolved.db_port, resolved.db_name, resolved.db_user,
                      resolved.query_max_rows, resolved.complex_core_max_retries, resolved.complex_aux_max_retries,
                      resolved.complex_max_resume_rounds, resolved.feature_complex_plan_execute,
                      resolved.feature_rules, resolved.feature_guards, resolved.feature_indicator_knowledge))
        return hashlib.sha256(value.encode()).hexdigest()

    def check_resume_state(state: dict[str, Any], revision: str) -> None:
        if (state.get("catalog_revision") != revision or state.get("execution_version") != app.state.source_version
                or state.get("policy_fingerprint") != policy_fingerprint()):
            raise ResumeConflict("指标口径、执行代码或数据权限已变更，请终止旧分析后重新查询，避免混用结果。")

    def execute_query(question: str, request_id: str) -> QueryResponse:
        service = app.state.query_service
        if service is None:
            raise ChatBIError(ErrorCode.MODEL_UNAVAILABLE, "查询服务配置不完整，请检查就绪状态。")
        while not app.state.catalog_lock.acquire(timeout=.2):
            checkpoint()
        try:
            checkpoint()
            revision, knowledge = app.state.catalog.snapshot()
            resume = resumed_state()
            if resume:
                try:
                    check_resume_state(resume, revision)
                except ResumeConflict as conflict:
                    item = app.state.history.get(request_id)
                    public = dict((item or {}).get("response", {}).get("suspension", {}))
                    public.update(message=str(conflict), options=["abort"])
                    raise AnalysisSuspended(resume, public) from conflict
            if app.state.catalog_revision != revision:
                refresh_query_catalog(service, knowledge)
                app.state.catalog_revision = revision
            try:
                options = query_options() or (app.state.history.get(request_id) or {}).get("request_options") or {}
                context = options.get("analysis_context") or conversation_context(question)
                actual = effective_question(question, context)
                if context.get("parent_query_id") and re.search(r"(?:整理|汇总|合并).*(?:报告|分析)|(?:这几步|这几轮).*报告", question):
                    from chatbi.application.conversation_report import compile_report
                    parent_response = (app.state.history.get(context["parent_query_id"]) or {}).get("response")
                    if not parent_response:
                        raise ChatBIError(ErrorCode.INPUT_INVALID, "历史报告已清理，请重新分析。")
                    combined = compile_report(context, app.state.history.get)
                    return QueryResponse.model_validate({**parent_response, **combined, "question": question,
                        "request_id": request_id, "sql": "", "step_results": [], "plan": None, "visualization_spec": [],
                        "query_type": "simple", "agent_mode": "conversation_report", "analysis_context": context,
                        "report": combined["report_markdown"], "report_source": "conversation_report", "token_usage": {},
                        "timings_ms": {}, "validation": {"status": "historical_evidence", "limitations": ["汇总历史报告，未重新查询。"]}})
                if resume and resume.get("kind") == "clarification":
                    actual = resume.get("resolved_question", actual)
                    if resume.get("resolved_question"):
                        context["turns"][-1] = {"question": actual, "original_question": question}
                if not isinstance(service, ChatBIQueryService):
                    require_clarification(actual)
                before = dict(getattr(service.llm, "usage", {})) if hasattr(service, "llm") else {}
                with analysis_options({**options, "analysis_context": context, "clarify_before_query": True}):
                    result = service.query(actual, request_id=request_id)
                if result.query_type == "non_query":
                    context = {**context, "turns": context.get("turns", [])[:-1], "conversation_only": True}
                usage = {key: value - before.get(key, 0) for key, value in getattr(getattr(service, "llm", None), "usage", {}).items()}
            except AnalysisSuspended as exc:
                exc.state.update(catalog_revision=revision, execution_version=app.state.source_version,
                                 policy_fingerprint=policy_fingerprint())
                raise
            checkpoint()
        finally:
            app.state.catalog_lock.release()
        return QueryResponse(
            request_id=result.request_id,
            question=question,
            sql=result.sql,
            columns=list(result.columns),
            rows=[list(row) for row in result.rows],
            row_count=result.row_count,
            truncated=result.truncated,
            schema_mode=result.schema_mode,
            query_type=result.query_type,
            intent_confidence=result.intent_confidence,
            intent_reason=result.intent_reason,
            intent_source=result.intent_source,
            semantic_mode=result.semantic.mode,
            recalled_indicators=list(result.semantic.indicators),
            recalled_tables=list(result.semantic.tables),
            recalled_fields=list(result.semantic.fields),
            inferred_joins=list(result.semantic.joins),
            row_level_applied=result.row_level_applied,
            pipeline_stages=list(result.pipeline_stages),
            schema_reason=result.schema_reason,
            timings_ms=result.timings_ms,
            agent_mode=result.agent_mode,
            agent_status=result.agent_status,
            plan=result.plan,
            step_results=list(result.step_results),
            analysis_report=result.analysis_report,
            summary=result.summary,
            report=result.report,
            report_markdown=result.report_markdown,
            report_source=result.report_source,
            agent_timings_ms=result.agent_timings_ms,
            visualization_spec=build_visualizations(result),
            catalog_revision=revision,
            validation=result.validation,
            analysis_context=context,
            retrieval_diagnostics=result.retrieval_diagnostics,
            token_usage=usage,
            message=result.message,
        )

    def request_options(payload: QueryRequest) -> dict[str, Any]:
        if not payload.question.strip() or len(payload.question) > resolved.query_max_length:
            raise ChatBIError(ErrorCode.INPUT_INVALID)
        from chatbi.infrastructure.intent import obvious_non_query
        if obvious_non_query(payload.question):
            return {"analysis_context": {"turns": [{"question": payload.question}],
                    "parent_query_id": payload.parent_query_id}, "review_plan": False}
        parent = None
        if payload.parent_query_id:
            parent = app.state.history.get(payload.parent_query_id)
            if not parent or parent["status"] != "succeeded" or not parent.get("response") or parent["response"].get("history_payload_omitted") or parent["response"].get("query_type") == "non_query":
                raise HTTPException(409, "只能继续分析有完整结果的已完成记录。")
        try:
            context = conversation_context(payload.question, parent)
            if len(effective_question(payload.question, context)) > 9000:
                raise ValueError("对话上下文过长，请开启新分析。")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"analysis_context": context, "review_plan": payload.review_plan}

    @app.post("/api/v1/queries", response_model=QueryResponse, responses=error_responses)
    def create_query(payload: QueryRequest, request: Request) -> QueryResponse:
        # Compatibility endpoint; the browser uses background jobs for progress.
        query_id = uuid4().hex
        app.state.history.create(query_id, payload.question, "running", request_options(payload))
        try:
            response = execute_query(payload.question, query_id)
            response = response.model_copy(update={"request_id": request.state.request_id})
            app.state.history.finish(query_id, "succeeded", response=response.model_dump(mode="json"))
            return response
        except AnalysisSuspended as exc:
            app.state.history.suspend(query_id, exc.state, exc.public)
            exc.details["history_id"] = query_id
            raise
        except ChatBIError as exc:
            app.state.history.finish(query_id, "failed", error={"code": exc.code.value, "message": exc.public_message})
            raise
        except Exception as exc:
            app.state.history.finish(query_id, "failed", error={"code": "internal_error", "message": "查询失败，请重试。"})
            raise ChatBIError(ErrorCode.INTERNAL_ERROR) from exc

    @app.exception_handler(CatalogError)
    async def catalog_error(request: Request, exc: CatalogError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"code": "catalog_conflict", "message": str(exc),
                                                     "request_id": request.state.request_id})

    def local_catalog(request: Request, *, write: bool = False) -> MetricRegistry:
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        host = request.url.hostname
        peer = request.client.host if request.client else None
        test_call = resolved.app_env == "test" and peer == "testclient"
        if not test_call and (host not in local_hosts or peer not in local_hosts):
            raise HTTPException(403, "指标管理仅限本机访问")
        if write and request.headers.get("origin"):
            origin = urlparse(request.headers["origin"])
            if origin.netloc != request.url.netloc or origin.scheme != request.url.scheme:
                raise HTTPException(403, "拒绝跨站指标修改")
        return request.app.state.catalog

    @app.post("/api/v1/query-jobs", status_code=202)
    def submit_job(payload: QueryRequest, request: Request) -> dict[str, Any]:
        local_catalog(request, write=True)
        if not payload.question.strip() or len(payload.question) > resolved.query_max_length:
            raise ChatBIError(ErrorCode.INPUT_INVALID)
        if app.state.query_service is None:
            raise ChatBIError(ErrorCode.MODEL_UNAVAILABLE)
        try:
            query_id = app.state.jobs.submit(payload.question.strip(), request_options(payload))
        except Full:
            raise HTTPException(429, "查询队列已满，请稍后重试")
        return {"id": query_id, "events_url": f"/api/v1/query-jobs/{query_id}/events"}

    def history_item(query_id: str) -> dict[str, Any]:
        item = app.state.history.get(query_id)
        if item is None:
            raise HTTPException(404, "查询记录不存在")
        return item

    @app.get("/api/v1/history")
    def list_history(request: Request, limit: int = 30, offset: int = 0, favorites: bool = False) -> dict[str, Any]:
        local_catalog(request)
        return {"items": app.state.history.list_history(min(max(limit, 1), 100), max(offset, 0), favorites)}

    @app.get("/api/v1/history/{query_id}")
    def read_history(query_id: str, request: Request) -> dict[str, Any]:
        local_catalog(request)
        return {**history_item(query_id), "events": app.state.history.events(query_id)}

    @app.patch("/api/v1/history/{query_id}")
    def annotate_history(query_id: str, payload: HistoryAnnotation, request: Request) -> dict[str, Any]:
        local_catalog(request, write=True)
        history_item(query_id)
        app.state.history.annotate(query_id, payload.model_dump(exclude_unset=True, exclude_none=True))
        return history_item(query_id)

    @app.delete("/api/v1/history")
    def clear_history(request: Request) -> dict[str, int]:
        local_catalog(request, write=True)
        return {"deleted": app.state.history.clear()}

    @app.delete("/api/v1/history/{query_id}")
    def delete_history(query_id: str, request: Request) -> dict[str, int]:
        local_catalog(request, write=True)
        if history_item(query_id)["status"] not in TERMINAL:
            raise HTTPException(409, "请先取消并等待查询停止")
        return {"deleted": app.state.history.clear(query_id)}

    @app.post("/api/v1/query-jobs/{query_id}/cancel")
    def cancel_job(query_id: str, request: Request) -> dict[str, Any]:
        local_catalog(request, write=True)
        history_item(query_id)
        return {"accepted": app.state.jobs.cancel(query_id)}

    @app.post("/api/v1/query-jobs/{query_id}/resume", status_code=202)
    def resume_job(query_id: str, payload: ResumeRequest, request: Request) -> dict[str, Any]:
        local_catalog(request, write=True)
        item = history_item(query_id)
        if item["status"] != "suspended":
            raise HTTPException(409, "只有挂起任务可以续跑或终止")
        if payload.action == "abort":
            return {"id": query_id, "accepted": app.state.jobs.cancel(query_id)}
        state = app.state.history.checkpoint(query_id)
        if not state:
            raise HTTPException(409, "断点不存在，请重新查询")
        try:
            check_resume_state(state, app.state.catalog.snapshot()[0])
            def transform(current):
                if current.get("kind") == "clarification":
                    if payload.action != "clarify":
                        raise ResumeConflict("请先回答业务澄清问题。")
                    current["resolved_question"] = apply_answers(current["question"], current["questions"], payload.answers)
                    require_clarification(current["resolved_question"])
                if payload.action == "edit":
                    preview = preview_edits(current, payload.edits)
                    if preview["preview_token"] != payload.preview_token:
                        raise ResumeConflict("计划预览已过期，请重新预览后确认。")
                    current["plan"] = preview["plan"]
                    current["cached_results"] = [row for row in current.get("cached_results", []) if row["step_id"] not in preview["affected_steps"]]
                return current
            clarification = payload.clarification or ("业务口径已确认" if payload.answers else "")
            app.state.jobs.resume(query_id, payload.action, clarification, transform)
        except ResumeConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except Full:
            raise HTTPException(429, "查询队列已满，断点保留，请稍后继续")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": query_id, "events_url": f"/api/v1/query-jobs/{query_id}/events"}

    @app.post("/api/v1/query-jobs/{query_id}/plan-preview")
    def plan_preview(query_id: str, payload: PlanEditRequest, request: Request) -> dict[str, Any]:
        local_catalog(request, write=True)
        if history_item(query_id)["status"] != "suspended":
            raise HTTPException(409, "计划只能在挂起时修改，请先启用执行前确认计划。")
        state = app.state.history.checkpoint(query_id)
        if not state:
            raise HTTPException(409, "断点不存在")
        try:
            check_resume_state(state, app.state.catalog.snapshot()[0])
            return preview_edits(state, payload.edits)
        except ResumeConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/query-jobs/{query_id}/events")
    async def job_events(query_id: str, request: Request, after: int = 0) -> StreamingResponse:
        local_catalog(request)
        await run_in_threadpool(history_item, query_id)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")), 0)
        except ValueError:
            raise HTTPException(422, "无效事件序号")
        async def stream():
            nonlocal cursor
            ticks = 0
            while not await request.is_disconnected():
                for event in await run_in_threadpool(app.state.history.events, query_id, cursor):
                    cursor = event["sequence"]
                    yield f"id: {cursor}\nevent: progress\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                item = await run_in_threadpool(app.state.history.get, query_id)
                if item is None or item["status"] in TERMINAL:
                    yield "event: done\ndata: {}\n\n"
                    return
                ticks += 1
                if ticks % 30 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(.3)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/v1/runtime")
    def runtime_info() -> dict[str, Any]:
        disk_version = source_version()
        return {"source_version": app.state.source_version, "disk_version": disk_version,
                "restart_required": disk_version != app.state.source_version,
                "features": ["query_jobs", "sse_progress", "persistent_history", "metric_registry", "visualization", "failure_control", "checkpoint_resume",
                             "result_validation", "conversation", "local_replanning", "clarification", "drilldown", "evaluation_dashboard", "analysis_console"]}

    @app.post("/api/v1/history/{query_id}/drilldown")
    def drilldown(query_id: str, payload: DrilldownRequest, request: Request) -> dict[str, Any]:
        from chatbi.application.drilldown import source_result, build_detail_sql
        local_catalog(request, write=True)
        item = history_item(query_id)
        if item["status"] != "succeeded" or not item.get("response"):
            raise HTTPException(409, "只有已完成结果可以下钻。")
        response = item["response"]
        service = app.state.query_service
        with app.state.catalog_lock:
            if response.get("catalog_revision") != app.state.catalog.snapshot()[0]:
                raise HTTPException(409, "指标版本已变更，请重新分析后下钻。")
            try:
                sql = build_detail_sql(source_result(response, payload.step_id), payload.dimension, payload.value)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            secured = service.security.secure(sql, service.user_context)
            columns, rows = service.database.execute(secured)
            columns, rows = service.security.mask_result(columns, rows, service.user_context)
        return {"columns": columns, "rows": rows, "sql": secured.sql,
                "truncated": secured.limited and len(rows) >= secured.max_rows,
                "note": "按原查询时间和筛选读取当前数据库明细；关联行不保证一行一单。达到行数上限时仅显示部分，不应用于核对总量。",
                "source_query_id": query_id, "source_step_id": payload.step_id}

    @app.get("/api/v1/history/{query_id}/evidence")
    def read_evidence(query_id: str, request: Request, step_id: str, row_index: int, column: str) -> dict[str, Any]:
        from chatbi.application.drilldown import evidence_rows
        local_catalog(request)
        try:
            return evidence_rows(history_item(query_id).get("response") or {}, step_id, row_index, column)
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, "证据引用不存在或已清理。") from exc

    @app.get("/api/v1/observability")
    def observability(request: Request) -> dict[str, Any]:
        from chatbi.application.observability import evaluation_dashboard
        local_catalog(request)
        with app.state.history.connect() as db:
            statuses = {row[0]: row[1] for row in db.execute("SELECT status,COUNT(*) FROM queries GROUP BY status")}
        semantic = getattr(app.state.query_service, "simple_semantic", None)
        return {"evaluation": evaluation_dashboard(Path("reports/release-quality.json"), app.state.source_version),
                "runtime": {"statuses": statuses, "retained_samples": sum(statuses.values())},
                "retrieval": getattr(semantic, "diagnostics", {"encoder": "static_catalog", "fallback": True})}

    @app.get("/api/v1/metrics")
    def list_metrics(request: Request) -> dict[str, Any]:
        catalog = local_catalog(request)
        revision, knowledge = catalog.snapshot()
        return {"items": catalog.list_metrics(), "revision": revision, "active_count": len(knowledge.indicators)}

    @app.get("/api/v1/metrics/{metric_id}")
    def metric_detail(metric_id: str, request: Request) -> dict[str, Any]:
        return local_catalog(request).detail(metric_id)

    @app.post("/api/v1/metrics", status_code=201)
    def new_metric(payload: MetricDraft, request: Request) -> dict[str, Any]:
        return local_catalog(request, write=True).create_draft(payload)

    @app.post("/api/v1/metrics/{metric_id}/versions", status_code=201)
    def new_metric_version(metric_id: str, payload: MetricDraft, request: Request) -> dict[str, Any]:
        return local_catalog(request, write=True).create_draft(payload, metric_id)

    @app.post("/api/v1/metrics/{metric_id}/versions/{version}/{action}")
    def decide_metric(metric_id: str, version: int, action: str, payload: CatalogDecision, request: Request) -> dict[str, Any]:
        return local_catalog(request, write=True).transition(metric_id, version, action, payload)

    @app.get("/health/live", response_model=HealthResponse)
    async def live() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/health/ready", response_model=HealthResponse)
    async def ready(request: Request) -> JSONResponse:
        service = request.app.state.query_service
        model_configured = resolved.openai_api_key is not None
        intent_configured = (
            not resolved.feature_intent_router or resolved.intent_api_key is not None
        )
        intent_classifier = getattr(service, "intent_classifier", None)
        intent_runtime_error = getattr(intent_classifier, "last_error", None)
        database_ok = bool(service and service.database.validate_connection())
        index_ok = indexes_ready(resolved)
        simple_semantic_enabled = bool(service and getattr(service, "simple_semantic", None))
        components = {
            "application": ComponentHealth(status="ok"),
            "database": ComponentHealth(status="ok" if database_ok else "unavailable"),
            "model": ComponentHealth(status="ok" if model_configured else "unavailable"),
            "intent_model": ComponentHealth(
                status=(
                    "fallback" if intent_runtime_error
                    else ("ok" if intent_configured else "unavailable")
                ),
                detail=(
                    f"{resolved.intent_model} 调用失败，已启用本地规则回退"
                    if intent_runtime_error
                    else (resolved.intent_model if intent_configured else "INTENT_API_KEY 未配置")
                ),
            ),
            "schema_index": ComponentHealth(
                status="ok" if index_ok else "fallback",
                detail=None if index_ok else "静态 Schema 回退可用",
            ),
            "simple_semantic": ComponentHealth(
                status="ok" if simple_semantic_enabled else "disabled",
                detail=("Chroma" if simple_semantic_enabled else "等待兼容的 Chroma 运行环境"),
            ),
        }
        is_ready = service is not None and database_ok and model_configured and intent_configured
        content = HealthResponse(
            status="ready" if is_ready else "not_ready",
            components=components,
        ).model_dump()
        return JSONResponse(status_code=200 if is_ready else 503, content=content)

    web_dir = Path(__file__).resolve().parents[1] / "web"
    if web_dir.is_dir():
        app.mount("/static", StaticFiles(directory=web_dir), name="static")

        @app.get("/", include_in_schema=False)
        async def web_index() -> FileResponse:
            index = web_dir / "index.html"
            if not index.exists():
                raise HTTPException(status_code=404)
            return FileResponse(index)

        @app.get("/metrics", include_in_schema=False)
        async def metrics_page() -> FileResponse:
            return FileResponse(web_dir / "metrics.html")

        @app.get("/observability", include_in_schema=False)
        async def observability_page() -> FileResponse:
            return FileResponse(web_dir / "observability.html")

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("chatbi.api.app:app", host=settings.app_host, port=settings.app_port, reload=False)
