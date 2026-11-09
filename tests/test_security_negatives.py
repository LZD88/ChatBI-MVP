from __future__ import annotations

import pytest

from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.domain.security import QuerySecurityManager, SecuredQuery


class RecordingDatabase:
    def __init__(self) -> None:
        self.queries: list[SecuredQuery] = []

    def execute(self, query: SecuredQuery) -> tuple[list[str], list[tuple]]:
        self.queries.append(query)
        return ["order_id"], []


@pytest.mark.parametrize(
    ("scenario", "model_sql"),
    [
        ("prompt_injection_drop", "DROP TABLE sales_orders"),
        ("prompt_injection_file_read", "SELECT LOAD_FILE('C:/Windows/win.ini')"),
        ("sql_concatenation", "SELECT order_id FROM sales_orders; DELETE FROM sales_orders"),
        ("comment_multi_statement", "SELECT order_id FROM sales_orders; -- safe\nDROP TABLE dim_products"),
        ("system_table", "SELECT table_name FROM information_schema.tables"),
        ("dangerous_sleep", "SELECT SLEEP(10) FROM sales_orders"),
        ("dangerous_benchmark", "SELECT BENCHMARK(1000000, SHA1('x')) FROM sales_orders"),
        ("dangerous_command", "SELECT order_id FROM sales_orders INTO OUTFILE 'result.txt'"),
    ],
)
def test_malicious_model_sql_is_rejected_before_database(
    scenario: str,
    model_sql: str,
) -> None:
    security = QuerySecurityManager(max_rows=20)
    database = RecordingDatabase()

    with pytest.raises(ChatBIError) as captured:
        secured = security.secure(model_sql)
        database.execute(secured)

    assert captured.value.code is ErrorCode.SQL_REJECTED, scenario
    assert database.queries == [], scenario


def test_oversized_result_request_is_constrained_before_database() -> None:
    security = QuerySecurityManager(max_rows=20)
    database = RecordingDatabase()
    raw_sql = "SELECT order_id FROM sales_orders LIMIT 1000000"

    secured = security.secure(raw_sql)
    database.execute(secured)

    assert secured.limited is True
    assert "LIMIT 20" in secured.sql
    assert "1000000" not in secured.sql
    assert database.queries == [secured]
