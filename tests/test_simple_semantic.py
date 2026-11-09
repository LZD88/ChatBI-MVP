from __future__ import annotations

import os

import pytest

from chatbi.infrastructure.simple_semantic import ChromaSimpleSemanticPipeline
from chatbi.settings import Settings


pytestmark = pytest.mark.skipif(
    os.getenv("CHATBI_CHROMA_TEST") != "1",
    reason="requires the isolated Chroma HTTP service",
)


def pipeline(tmp_path):
    return ChromaSimpleSemanticPipeline(
        Settings(_env_file=None, simple_chroma_dir=tmp_path / "simple", simple_table_top_k=4,
                 simple_field_top_k=10)
    )


def test_chroma_builds_three_collections_for_all_tables_and_fields(tmp_path) -> None:
    semantic = pipeline(tmp_path)
    assert semantic.indicators.count() == 61
    assert semantic.tables.count() == 30
    assert semantic.fields.count() > 150


def test_simple_revenue_query_recalls_indicator_schema_and_join(tmp_path) -> None:
    result = pipeline(tmp_path).resolve("按地区统计已完成订单的销售额")
    assert result.mode == "chroma"
    assert "收入" in result.indicators
    assert "sales_orders" in result.schema.tables
    assert "exchange_rates" in result.schema.tables
    assert "sales_orders.net_amount" in result.fields
    assert any("sales_orders.currency = exchange_rates.currency" in join for join in result.joins)
    assert "Chroma 动态精简 Schema" in result.schema.text


def test_join_inference_connects_purchase_orders_to_suppliers(tmp_path) -> None:
    result = pipeline(tmp_path).resolve("按供应商统计采购订单总金额")
    assert "purchase_orders" in result.schema.tables
    assert "dim_suppliers" in result.schema.tables
    assert any("purchase_orders.supplier_id = dim_suppliers.supplier_id" in join for join in result.joins)


def test_unrelated_tables_are_not_all_injected(tmp_path) -> None:
    result = pipeline(tmp_path).resolve("按供应商统计采购订单总金额")
    assert len(result.schema.tables) < 30
    assert "finance_expenses" not in result.schema.tables


def test_exact_indicator_does_not_expand_to_related_metric(tmp_path) -> None:
    result = pipeline(tmp_path).resolve("各产品线的毛利是多少？")
    assert result.indicators == ("毛利",)
    assert "finance_expenses" not in result.schema.tables


@pytest.mark.parametrize(
    ("question", "indicator", "required_tables"),
    [
        ("采购收货率是多少", "采购收货率", {"purchase_order_items", "purchase_orders"}),
        ("最新可用库存是多少", "可用库存", {"inventory_balances"}),
        ("各产品的生产良率", "生产良率", {"production_outputs", "production_orders"}),
        ("按产品统计检验合格率", "检验合格率", {"quality_inspections", "production_orders"}),
        ("各设备的维护成本", "设备维护成本", {"equipment_maintenance", "equipment"}),
        ("预算执行率是多少", "预算执行率", {"budgets", "dim_cost_centers"}),
    ],
)
def test_extended_indicator_domains_recall_governed_sources(
    tmp_path, question: str, indicator: str, required_tables: set[str]
) -> None:
    result = pipeline(tmp_path).resolve(question)
    assert result.indicators == (indicator,)
    assert required_tables.issubset(result.schema.tables)
