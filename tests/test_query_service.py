from __future__ import annotations

from dataclasses import dataclass

import pytest

from chatbi.application.query_service import ChatBIQueryService
from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.domain.models import QueryIntent
from chatbi.domain.schema import SchemaSelection
from chatbi.domain.security import QuerySecurityManager, SecuredQuery
from chatbi.settings import Settings
from database import QueryExecutionError


@dataclass
class FakeLLM:
    sql: str = "SELECT COUNT(*) AS order_count FROM sales_orders"
    calls: int = 0

    def generate_sql(self, system_msg: str, prompt: str) -> str:
        self.calls += 1
        assert "sales_orders" in prompt
        return self.sql


class FakeSchema:
    def select(self, question: str) -> SchemaSelection:
        return SchemaSelection("表：sales_orders（相关字段：order_id, order_status）", "semantic", ("sales_orders",))


@dataclass
class FakeIntent:
    query_type: str = "simple"
    calls: int = 0

    def classify(self, question: str) -> QueryIntent:
        self.calls += 1
        return QueryIntent(self.query_type, 0.92, "固定测试分类", "qwen")


class FakeDatabase:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    def execute(self, query: SecuredQuery):
        self.calls += 1
        if self.error:
            raise self.error
        return ["order_count"], [(7,)]

    def validate_connection(self) -> bool:
        return True

    def close(self) -> None:
        return None


def make_service(*, llm: FakeLLM | None = None, database: FakeDatabase | None = None,
                 intent: FakeIntent | None = None, max_length: int = 1000):
    return ChatBIQueryService(
        llm=llm or FakeLLM(),
        schema_provider=FakeSchema(),
        security=QuerySecurityManager(max_rows=10),
        database=database or FakeDatabase(),
        settings=Settings(_env_file=None, query_max_length=max_length),
        intent_classifier=intent or FakeIntent(),
    )


def test_successful_pipeline_returns_structured_result() -> None:
    result = make_service().query("查询订单数", request_id="req-1")
    assert result.request_id == "req-1"
    assert result.columns == ("订单数量",)
    assert result.rows == ((7,),)
    assert result.schema_mode == "semantic"
    assert result.query_type == "simple"
    assert result.intent_source == "qwen"
    assert "sql_ast_safety" in result.pipeline_stages
    assert "connection_pool_execution" in result.pipeline_stages
    assert result.row_level_applied is False
    assert set(result.timings_ms) == {"intent", "indicator", "schema", "prompt", "model", "security", "database", "total"}


def test_complex_intent_injects_planning_guidance() -> None:
    class InspectingLLM(FakeLLM):
        def generate_sql(self, system_msg: str, prompt: str) -> str:
            assert "复杂查询规划要求" in prompt
            return super().generate_sql(system_msg, prompt)

    result = make_service(llm=InspectingLLM(), intent=FakeIntent("complex")).query("收入和毛利趋势")
    assert result.query_type == "complex"


def test_result_columns_are_forced_to_chinese_even_when_model_aliases_are_english() -> None:
    class WarehouseDatabase(FakeDatabase):
        def execute(self, query: SecuredQuery):
            return ["warehouse_id", "warehouse_name", "available_inventory"], [(1, "华东成品仓", 157)]

    result = make_service(database=WarehouseDatabase()).query("按仓库统计最新可用库存")
    assert result.columns == ("仓库ID", "仓库名称", "可用库存")


@pytest.mark.parametrize(
    ("question", "code"),
    [("   ", ErrorCode.INPUT_INVALID), ("too long", ErrorCode.INPUT_TOO_LONG)],
)
def test_invalid_question_short_circuits_model_and_database(question: str, code: ErrorCode) -> None:
    llm = FakeLLM()
    database = FakeDatabase()
    service = make_service(llm=llm, database=database, max_length=3)
    with pytest.raises(ChatBIError) as exc:
        service.query(question)
    assert exc.value.code == code
    assert llm.calls == 0
    assert database.calls == 0


@pytest.mark.parametrize(
    ("db_error", "code"),
    [
        (QueryExecutionError("query_timeout", "secret stack"), ErrorCode.DATABASE_TIMEOUT),
        (QueryExecutionError("connection_error", "secret stack"), ErrorCode.DATABASE_UNAVAILABLE),
        (QueryExecutionError("sql_syntax", "secret stack"), ErrorCode.DATABASE_QUERY_FAILED),
    ],
)
def test_database_errors_are_mapped_without_raw_details(db_error: Exception, code: ErrorCode) -> None:
    with pytest.raises(ChatBIError) as exc:
        make_service(database=FakeDatabase(db_error)).query("订单数")
    assert exc.value.code == code
    assert "secret stack" not in exc.value.public_message


def test_unknown_errors_are_mapped_to_internal_error() -> None:
    class BrokenSchema:
        def select(self, question: str):
            raise RuntimeError("private failure")

    service = make_service()
    service.schema_provider = BrokenSchema()
    with pytest.raises(ChatBIError) as exc:
        service.query("订单数")
    assert exc.value.code == ErrorCode.INTERNAL_ERROR
    assert "private failure" not in exc.value.public_message
