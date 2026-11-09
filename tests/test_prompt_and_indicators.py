import json

import pytest

from indicator_knowledge import IndicatorKnowledge, REQUIRED_INDICATOR_FIELDS
from prompt_builder import build_prompt


def test_prompt_uses_explicit_schema_without_hidden_import() -> None:
    custom = "表：tiny_table（相关字段：value）"
    _, prompt = build_prompt(
        "查询值", schema_text=custom, use_schema_linking=True, use_few_shot=False
    )
    assert custom in prompt
    assert "dim_customers" not in prompt


def test_static_schema_is_used_when_explicit_schema_absent() -> None:
    _, prompt = build_prompt("查询订单")
    assert "sales_orders" in prompt


def test_prompt_limits_output_to_requested_metrics() -> None:
    _system, prompt = build_prompt("各产品线的毛利是多少？", use_rules=True, use_guards=True)
    assert "只返回用户明确请求的维度和指标" in prompt
    assert "不要额外输出收入、成本等中间指标" in prompt
    assert "dim_products" in prompt
    assert "所有 SELECT 输出列必须使用简洁、准确的中文别名" in prompt
    assert "available_inventory" in prompt


def test_complex_query_prompt_contains_planning_contract() -> None:
    _system, prompt = build_prompt("比较收入和毛利趋势", query_type="complex")
    assert "复杂查询规划要求" in prompt
    assert "最终只能输出一条只读 SQL" in prompt


def test_revenue_knowledge_includes_net_amount_status_and_exchange_rate() -> None:
    knowledge = IndicatorKnowledge().build_knowledge_block("查询销售额")
    assert "net_amount" in knowledge
    assert "order_status = 'completed'" in knowledge
    assert "rate_to_cny" in knowledge


def test_profit_includes_dependencies_and_expense_total_has_no_subitems() -> None:
    knowledge = IndicatorKnowledge().build_knowledge_block("查询利润和期间费用")
    assert "指标：利润" in knowledge
    assert "指标：毛利" in knowledge
    assert "指标：期间费用" in knowledge
    formula_line = next(line for line in knowledge.splitlines() if "selling_expense" in line and "SQL公式" in line)
    assert "marketing_expense" not in formula_line
    assert "logistics_expense" not in formula_line
    assert "warranty_expense" not in formula_line


def test_cost_and_gross_profit_knowledge_are_available() -> None:
    knowledge = IndicatorKnowledge().build_knowledge_block("销售成本和毛利")
    assert "material_cost" in knowledge
    assert "labor_cost" in knowledge
    assert "指标：毛利" in knowledge


def test_every_indicator_uses_the_governed_six_field_contract() -> None:
    knowledge = IndicatorKnowledge()
    for definition in knowledge.indicators.values():
        assert set(REQUIRED_INDICATOR_FIELDS).issubset(definition)
        assert all(definition[field] not in (None, "") for field in REQUIRED_INDICATOR_FIELDS)


def test_profit_recursively_expands_all_indicator_dependencies() -> None:
    knowledge = IndicatorKnowledge().build_knowledge_block("查询利润")
    assert "指标：利润" in knowledge
    assert "指标：毛利" in knowledge
    assert "指标：收入" in knowledge
    assert "指标：销售成本" in knowledge
    assert "指标：期间费用" in knowledge
    assert "时间字段：" in knowledge


def test_indicator_loader_rejects_missing_fields_and_dependency_cycles(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps({"indicators": [{"name": "坏指标"}]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="missing required fields"):
        IndicatorKnowledge(missing)

    cycle = tmp_path / "cycle.json"
    base = {
        "definition": "测试",
        "formula": "1",
        "data_source": "dual",
        "time_field": "created_at",
    }
    cycle.write_text(json.dumps({"indicators": [
        {**base, "name": "A", "depends_on": ["B"]},
        {**base, "name": "B", "depends_on": ["A"]},
    ]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="dependency cycle"):
        IndicatorKnowledge(cycle)


def test_indicator_catalog_covers_mvp_business_domains() -> None:
    knowledge = IndicatorKnowledge()
    assert len(knowledge.indicators) == 61
    expected = {
        "毛利率", "采购收货率", "可用库存", "生产良率", "检验合格率",
        "设备维护成本", "发运数量", "客户回款率", "采购付款率", "预算执行率",
        "在职员工数", "仓库容量",
    }
    assert expected.issubset(knowledge.indicators)


def test_specific_indicator_alias_suppresses_overlapping_generic_metric() -> None:
    knowledge = IndicatorKnowledge()
    assert knowledge.detect_indicators("按设备统计维护成本") == ["设备维护成本"]
    assert knowledge.detect_indicators("各产品线的毛利率") == ["毛利率"]
