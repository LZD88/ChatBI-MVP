from __future__ import annotations

from typing import Any

import pymysql
import pytest

from chatbi.domain.security import QuerySecurityManager
from database import DatabaseClient, QueryExecutionError


class FakeCursor:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.executed: list[tuple[str, Any]] = []
        self.description = [("order_count",)]

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))
        if not sql.startswith("SET SESSION") and self.error is not None:
            raise self.error

    def fetchall(self) -> tuple[tuple[int]]:
        return ((3,),)


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.fake_cursor = cursor
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.fake_cursor

    def close(self) -> None:
        self.closed = True


def make_client(error: Exception | None = None) -> tuple[DatabaseClient, FakeCursor, FakeConnection]:
    cursor = FakeCursor(error=error)
    connection = FakeConnection(cursor)
    client = DatabaseClient(
        db_config={},
        connection_factory=lambda: connection,
        query_timeout_seconds=7,
        slow_query_threshold_ms=999999,
    )
    return client, cursor, connection


def secured_query():
    return QuerySecurityManager().secure("SELECT COUNT(*) AS order_count FROM sales_orders")


def test_execute_only_accepts_secured_query() -> None:
    client, _, _ = make_client()
    with pytest.raises(TypeError, match="SecuredQuery"):
        client.execute("SELECT 1")  # type: ignore[arg-type]


def test_executes_timeout_then_secured_query_and_closes_connection() -> None:
    client, cursor, connection = make_client()

    columns, rows = client.execute(secured_query())

    assert columns == ["order_count"]
    assert rows == [(3,)]
    assert cursor.executed[0] == ("SET SESSION MAX_EXECUTION_TIME = %s", (7000,))
    assert cursor.executed[1][0].startswith("SELECT COUNT(*)")
    assert connection.closed is True


@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (pymysql.err.ProgrammingError(1064, "syntax"), "sql_syntax"),
        (pymysql.err.OperationalError(1142, "denied"), "permission_denied"),
        (pymysql.err.OperationalError(3024, "timeout"), "query_timeout"),
        (pymysql.err.OperationalError(2003, "connect"), "connection_error"),
        (pymysql.err.OperationalError(1105, "other"), "execution_error"),
    ],
)
def test_translates_database_errors(error: Exception, error_type: str) -> None:
    client, _, connection = make_client(error)

    with pytest.raises(QueryExecutionError) as exc:
        client.execute(secured_query())

    assert exc.value.error_type == error_type
    assert connection.closed is True
