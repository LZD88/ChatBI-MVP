from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from chatbi.api.app import create_app
from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.domain.models import QueryResult
from chatbi.settings import Settings


class FakeDatabase:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.closed = False

    def validate_connection(self) -> bool:
        return self.healthy

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeService:
    database: FakeDatabase
    error: ChatBIError | None = None

    def query(self, question: str, request_id: str | None = None) -> QueryResult:
        if self.error:
            raise self.error
        return QueryResult(
            request_id=request_id or "generated",
            question=question.strip(),
            sql="SELECT COUNT(*) AS order_count FROM sales_orders",
            columns=("order_count",),
            rows=((7,),),
            row_count=1,
            truncated=False,
            schema_mode="fallback",
            timings_ms={"total": 1.2},
        )


def settings(**kwargs) -> Settings:
    base = {"_env_file": None, "openai_api_key": "test-key", "intent_api_key": "intent-key", "db_user": "reader", "db_password": "secret"}
    base.update(kwargs)
    return Settings(**base)


def test_query_api_success_and_request_id_contract() -> None:
    app = create_app(settings(), service_factory=lambda _: FakeService(FakeDatabase()))
    with TestClient(app) as client:
        response = client.post("/api/v1/queries", json={"question": "订单数"}, headers={"X-Request-ID": "req-api"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-api"
    assert response.json()["request_id"] == "req-api"
    assert response.json()["rows"] == [[7]]
    assert response.json()["query_type"] == "simple"
    assert response.json()["semantic_mode"] == "not_applicable"


def test_query_api_validation_for_missing_and_invalid_json() -> None:
    app = create_app(settings(), service_factory=lambda _: FakeService(FakeDatabase()))
    with TestClient(app) as client:
        missing = client.post("/api/v1/queries", json={})
        invalid = client.post("/api/v1/queries", content="not-json", headers={"content-type": "application/json"})
    assert missing.status_code == invalid.status_code == 422
    assert missing.json()["code"] == invalid.json()["code"] == "input_invalid"


def test_domain_error_is_mapped_to_http_response() -> None:
    service = FakeService(FakeDatabase(), ChatBIError(ErrorCode.SQL_REJECTED))
    app = create_app(settings(), service_factory=lambda _: service)
    with TestClient(app) as client:
        response = client.post("/api/v1/queries", json={"question": "危险请求"})
    assert response.status_code == 422
    assert response.json()["code"] == "sql_rejected"
    assert "堆栈" not in response.text


def test_health_endpoints_report_ready_and_close_resources(tmp_path) -> None:
    service = FakeService(FakeDatabase(True))
    app = create_app(settings(chroma_persist_dir=tmp_path), service_factory=lambda _: service)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["components"]["schema_index"]["status"] == "fallback"
    assert service.database.closed is True


def test_health_reports_database_and_model_configuration_failures(tmp_path) -> None:
    unconfigured = Settings(_env_file=None, chroma_persist_dir=tmp_path)
    app = create_app(unconfigured, service_factory=lambda _: FakeService(FakeDatabase(False)))
    with TestClient(app) as client:
        ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["components"]["database"]["status"] == "unavailable"
    assert ready.json()["components"]["model"]["status"] == "unavailable"
    assert ready.json()["components"]["intent_model"]["status"] == "unavailable"


def test_startup_configuration_error_keeps_liveness_but_disables_query() -> None:
    def fail(_: Settings):
        raise ValueError("Missing required runtime settings: OPENAI_API_KEY")
    app = create_app(Settings(_env_file=None), service_factory=fail)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.post("/api/v1/queries", json={"question": "收入"}).status_code == 503


def test_openapi_contract_contains_public_routes_and_models() -> None:
    app = create_app(settings(), service_factory=lambda _: FakeService(FakeDatabase()))
    schema = app.openapi()
    assert {"/api/v1/queries", "/health/live", "/health/ready"}.issubset(schema["paths"])
    query = schema["paths"]["/api/v1/queries"]["post"]
    assert query["responses"]["200"]["content"]["application/json"]["schema"]
    assert query["responses"]["422"]["content"]["application/json"]["schema"]
    assert {"QueryRequest", "QueryResponse", "ErrorResponse", "HealthResponse"}.issubset(schema["components"]["schemas"])
