import pytest

from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.domain.security import QuerySecurityManager, RowLevelFilter, UserContext


@pytest.fixture
def security() -> QuerySecurityManager:
    return QuerySecurityManager(max_rows=25, sensitive_fields={"customer_name"})


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO sales_orders(order_id) VALUES (1)",
        "UPDATE sales_orders SET order_status='completed'",
        "DELETE FROM sales_orders",
        "DROP TABLE sales_orders",
        "START TRANSACTION",
        "SELECT * FROM sales_orders; DELETE FROM sales_orders",
        "SELECT * FROM sales_orders INTO OUTFILE 'x'",
        "not valid sql !!!",
    ],
)
def test_rejects_non_read_only_or_invalid_sql(security: QuerySecurityManager, sql: str) -> None:
    with pytest.raises(ChatBIError) as exc:
        security.secure(sql)
    assert exc.value.code == ErrorCode.SQL_REJECTED


def test_allows_select_cte_and_subquery(security: QuerySecurityManager) -> None:
    result = security.secure(
        "WITH completed AS (SELECT product_id, net_amount FROM sales_orders "
        "WHERE order_status='completed') SELECT product_id, net_amount FROM completed"
    )
    assert "WITH completed AS" in result.sql
    assert "LIMIT 25" in result.sql


def test_rejects_unknown_and_system_tables(security: QuerySecurityManager) -> None:
    for sql in ("SELECT * FROM users", "SELECT * FROM information_schema.tables"):
        with pytest.raises(ChatBIError):
            security.secure(sql)


def test_rejects_unknown_and_sensitive_fields(security: QuerySecurityManager) -> None:
    for sql in (
        "SELECT made_up FROM sales_orders",
        "SELECT customer_name FROM dim_customers",
    ):
        with pytest.raises(ChatBIError):
            security.secure(sql)


def test_validates_alias_qualified_fields(security: QuerySecurityManager) -> None:
    result = security.secure(
        "SELECT o.order_id, p.product_line FROM sales_orders o "
        "JOIN dim_products p ON o.product_id=p.product_id"
    )
    assert result.tables == ("dim_products", "sales_orders")


def test_allows_extended_bi_tables() -> None:
    security = QuerySecurityManager(max_rows=25)
    result = security.secure(
        "SELECT s.supplier_name, SUM(p.total_amount) AS purchase_amount "
        "FROM purchase_orders p JOIN dim_suppliers s ON p.supplier_id = s.supplier_id "
        "GROUP BY s.supplier_name"
    )
    assert result.tables == ("dim_suppliers", "purchase_orders")


def test_adds_and_caps_detail_limit(security: QuerySecurityManager) -> None:
    assert security.secure("SELECT order_id FROM sales_orders").sql.endswith("LIMIT 25")
    assert security.secure("SELECT order_id FROM sales_orders LIMIT 1000").sql.endswith("LIMIT 25")
    assert security.secure("SELECT order_id FROM sales_orders LIMIT 5").sql.endswith("LIMIT 5")


def test_does_not_limit_aggregate_query(security: QuerySecurityManager) -> None:
    result = security.secure("SELECT COUNT(*) AS order_count FROM sales_orders")
    assert "LIMIT" not in result.sql
    assert result.limited is False


def test_grouped_aggregate_is_still_limited(security: QuerySecurityManager) -> None:
    result = security.secure(
        "SELECT region, SUM(net_amount) AS revenue FROM sales_orders GROUP BY region"
    )
    assert result.sql.endswith("LIMIT 25")
    assert result.limited is True


def test_injects_row_level_policy_through_ast() -> None:
    security = QuerySecurityManager(max_rows=25)
    user = UserContext(
        "regional-user",
        frozenset({"sales_orders"}),
        (RowLevelFilter("sales_orders", "region", "华东"),),
    )
    result = security.secure(
        "SELECT o.order_id FROM sales_orders o WHERE o.order_status = 'completed'",
        user,
    )
    assert result.row_level_applied is True
    assert "o.region = '华东'" in result.sql
    assert "o.order_status = 'completed'" in result.sql


def test_row_level_policy_is_injected_inside_cte_scope() -> None:
    security = QuerySecurityManager(max_rows=25)
    user = UserContext(
        "regional-user",
        frozenset({"sales_orders"}),
        (RowLevelFilter("sales_orders", "region", "华东"),),
    )
    result = security.secure(
        "WITH scoped AS (SELECT order_id, region FROM sales_orders) SELECT order_id FROM scoped",
        user,
    )
    assert result.row_level_applied is True
    assert "sales_orders.region = '华东'" in result.sql


@pytest.mark.parametrize("function", ["SLEEP(1)", "BENCHMARK(10, SHA1('x'))", "LOAD_FILE('x')"])
def test_rejects_dangerous_functions(security: QuerySecurityManager, function: str) -> None:
    with pytest.raises(ChatBIError):
        security.secure(f"SELECT {function} FROM sales_orders")
