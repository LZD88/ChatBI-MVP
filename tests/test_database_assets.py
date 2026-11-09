from pathlib import Path

from scripts.init_database import load_sql, split_statements


DB_DIR = Path("db")


def test_schema_defines_expected_tables_and_constraints() -> None:
    schema = load_sql(DB_DIR / "01_schema.sql").lower()
    for table in (
        "dim_customers", "dim_products", "sales_orders", "exchange_rates", "finance_expenses"
    ):
        assert f"create table if not exists {table}" in schema
    assert "foreign key (customer_id)" in schema
    assert "foreign key (product_id)" in schema
    assert "foreign key (order_date, currency)" in schema


def test_extended_schema_brings_demo_database_to_thirty_tables() -> None:
    base_schema = load_sql(DB_DIR / "01_schema.sql").lower()
    extended_schema = load_sql(DB_DIR / "04_extended_schema.sql").lower()
    assert base_schema.count("create table if not exists") == 5
    assert extended_schema.count("create table if not exists") == 25
    for table in (
        "dim_suppliers", "purchase_orders", "inventory_balances", "production_orders",
        "quality_inspections", "equipment_maintenance", "shipments", "customer_payments",
        "supplier_payments", "budgets",
    ):
        assert f"create table if not exists {table}" in extended_schema


def test_extended_seed_is_idempotent_and_populates_every_new_table() -> None:
    seed = load_sql(DB_DIR / "05_extended_seed.sql")
    assert seed.count("ON DUPLICATE KEY UPDATE") == 25
    assert len(split_statements(seed)) == 26


def test_seed_is_idempotent_and_covers_business_dimensions() -> None:
    seed = load_sql(DB_DIR / "02_seed.sql")
    assert seed.count("ON DUPLICATE KEY UPDATE") == 5
    for value in ("欧洲", "北美", "亚太", "拉美", "中东非洲", "completed", "cancelled", "pending"):
        assert value in seed
    assert len(split_statements(seed)) == 6


def test_grants_are_select_only_and_password_is_a_token() -> None:
    grants = load_sql(DB_DIR / "03_grants.sql")
    assert "GRANT SELECT ON chatbi_mvp.*" in grants
    assert "GRANT INSERT" not in grants
    assert grants.count("__READER_PASSWORD__") == 4
