from __future__ import annotations

from typing import Any

from chatbi.domain.security import SecuredQuery
from database import QueryExecutionError
from evaluator import Evaluator


class FakeDatabase:
    def __init__(self) -> None:
        self.executed: list[SecuredQuery] = []

    def execute(self, query: SecuredQuery) -> tuple[list[str], list[tuple[Any, ...]]]:
        assert isinstance(query, SecuredQuery)
        self.executed.append(query)
        sql = query.sql.lower()
        if "order_id = 999999" in sql:
            raise QueryExecutionError("sql_syntax", "固定数据库错误")
        if "order_id = -1" in sql:
            return ["order_id"], []
        if "sum(net_amount)" in sql:
            alias = "generated_total" if "generated_total" in sql else "expected_total"
            return [alias], [(100,)]
        return ["value"], [(1,)]


def case(expected_sql: str, *, case_id: str = "case") -> dict[str, str]:
    return {"id": case_id, "category": "simple_aggregation", "difficulty": "simple",
            "question": "测试问题", "expected_sql": expected_sql}


def test_execution_equivalence_allows_different_aliases() -> None:
    db = FakeDatabase()
    evaluator = Evaluator(db_client=db, model_config={"model": "fixed-model", "temperature": 0})
    result = evaluator.evaluate_one(
        case("SELECT SUM(net_amount) AS expected_total FROM sales_orders"),
        lambda _question: "SELECT SUM(net_amount) AS generated_total FROM sales_orders",
    )
    assert result["execution_match"] is True
    assert result["exact_match"] is False
    assert all(isinstance(query, SecuredQuery) for query in db.executed)


def test_empty_results_are_equivalent() -> None:
    evaluator = Evaluator(db_client=FakeDatabase())
    sql = "SELECT order_id FROM sales_orders WHERE order_id = -1"
    result = evaluator.evaluate_one(case(sql), lambda _question: sql)
    assert result["execution_match"] is True
    assert result["detail"]["generated_row_count"] == 0


def test_security_rejection_happens_before_generated_database_execution() -> None:
    db = FakeDatabase()
    evaluator = Evaluator(db_client=db)
    result = evaluator.evaluate_one(case("SELECT COUNT(*) FROM sales_orders"),
                                    lambda _question: "SELECT SLEEP(1) FROM sales_orders")
    assert result["failure_class"] == "security_rejected"
    assert result["error_code"] == "sql_rejected"
    assert result["execution_failed"] is True
    assert len(db.executed) == 1


def test_database_failure_and_report_metrics_are_stable() -> None:
    evaluator = Evaluator(db_client=FakeDatabase(), model_config={"model": "fixed-model"})
    failed = evaluator.evaluate_one(
        case("SELECT COUNT(*) FROM sales_orders", case_id="failure"),
        lambda _question: "SELECT order_id FROM sales_orders WHERE order_id = 999999",
    )
    passed = evaluator.evaluate_one(
        case("SELECT COUNT(*) FROM sales_orders", case_id="success"),
        lambda _question: "SELECT COUNT(*) FROM sales_orders",
    )
    report = evaluator.build_report([passed, failed], benchmark_version="test-v1",
                                    database_snapshot="fixture-v1")
    assert failed["failure_class"] == "database_execution_failed"
    assert failed["error_code"] == "sql_syntax"
    assert report["metrics"]["execution_accuracy"] == 0.5
    assert report["metrics"]["exact_match_accuracy"] == 0.5
    assert report["metrics"]["execution_failure_rate"] == 0.5
    assert report["model_config"]["model"] == "fixed-model"
    assert report["failure_classes"] == {"database_execution_failed": 1}
