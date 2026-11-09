"""FastAPI application factory and local server entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import Any, Callable

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
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
}

logger = logging.getLogger("chatbi.api")


def build_query_service(settings: Settings) -> ChatBIQueryService:
    settings.validate_query_runtime()
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
            simple_semantic = ChromaSimpleSemanticPipeline(settings)
        except Exception as exc:
            # Chroma is an optional retrieval accelerator. A stopped VM must
            # not take the query API down; the query service retains its
            # governed static/legacy Schema path.
            logger.warning("simple_semantic_startup_fallback reason=%s", type(exc).__name__)
    llm = LLMClient(settings)
    indicators = IndicatorKnowledge()
    user_context = UserContext.demo_admin()
    plan_execute = None
    if settings.feature_complex_plan_execute:
        plan_execute = PlanAndExecuteService(
            PlannerAgent(llm, settings, indicators),
            ExecutionAgent(llm, security, database, settings, user_context),
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
        try:
            app.state.query_service = service_factory(resolved)
        except Exception as exc:
            app.state.startup_error = str(exc)
        yield
        service = app.state.query_service
        database = getattr(service, "database", None)
        if database is not None and hasattr(database, "close"):
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
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    }

    @app.post("/api/v1/queries", response_model=QueryResponse, responses=error_responses)
    async def create_query(payload: QueryRequest, request: Request) -> QueryResponse:
        service = request.app.state.query_service
        if service is None:
            raise ChatBIError(ErrorCode.MODEL_UNAVAILABLE, "查询服务配置不完整，请检查就绪状态。")
        result = service.query(payload.question, request_id=request.state.request_id)
        return QueryResponse(
            request_id=result.request_id,
            question=result.question,
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
        )

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

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("chatbi.api.app:app", host=settings.app_host, port=settings.app_port, reload=False)
