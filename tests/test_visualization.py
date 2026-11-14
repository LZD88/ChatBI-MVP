from datetime import date
from decimal import Decimal

import pytest

from chatbi.application.visualization import recommend


def source(columns, rows, **changes):
    return {"columns": columns, "rows": rows, **changes}


@pytest.mark.parametrize("question,columns,rows,kind", [
    ("总收入", ["收入"], [[Decimal("12.2")]], "kpi"),
    ("月度收入", ["月份", "收入"], [["2026-02", 200], ["2026-01", 100]], "line"),
    ("产品对比", ["产品线", "收入", "毛利率"], [["甲", 100, .2], ["乙", 200, .3]], "bar"),
    ("收入占比", ["产品线", "收入"], [["甲", 10], ["乙", 20]], "pie"),
    ("毛利率占比", ["产品线", "毛利率"], [["甲", .1], ["乙", .2]], "bar"),
    ("收入变动瀑布", ["原因", "收入变动"], [["新增", 100], ["减少", -20]], "waterfall"),
    ("按地区和产品收入", ["地区", "产品线", "收入"], [["华北", "甲", 10], ["华北", "乙", 20]], "stacked_bar"),
])
def test_chart_recommendations_are_deterministic(question, columns, rows, kind):
    spec = recommend(question, source(columns, rows))
    assert spec.type == kind
    if kind == "line":
        assert spec.rows[0][0] == "2026-01"


@pytest.mark.parametrize("columns,rows", [
    (["设备", "状态"], [["甲", "运行"]]),
    (["订单ID"], [[1001], [1002]]),
    (["产品线", "收入"], [["甲", None], ["乙", None]]),
    (["产品线", "收入"], [["甲", float("inf")], ["乙", 2]]),
    (["产品线", "收入"], [["甲", 10**20], ["乙", 2]]),
    (["产品线", "毛利率"], [["甲", .2], ["甲", .3]]),
    (["产品线", "月份", "收入"], [["甲", "2026-01", 2], ["乙", "2026-02", 3]]),
])
def test_ambiguous_or_invalid_data_remains_table_only(columns, rows):
    assert recommend("分析", source(columns, rows)) is None


def test_nulls_dates_and_truncation_scope_are_preserved():
    spec = recommend("趋势", source(["日期", "收入"], [[date(2026, 1, 1), 1], [date(2026, 1, 2), None]], truncated=True))
    assert spec.rows[1][1] is None
    assert "已截断" in spec.note
    spec = recommend("趋势", source(["日期", "收入"], [[f"2026-{i:03}", i] for i in range(110)]))
    assert len(spec.rows) == 100 and spec.sampled


def test_numeric_dimension_contract_cannot_become_metric_card():
    spec = recommend("查看编号", source(["对象", "数值"], [["甲", 1]], result_artifact={"columns": [{"name": "数值", "role": "dimension"}]}))
    assert spec is None


def test_complex_decimal_json_uses_output_types_without_mutating_evidence():
    result = source(["产品线", "收入"], [["甲", "12.500"], ["乙", "30.250"]],
                    result_artifact={"columns": [{"name": "产品线", "role": "dimension", "data_type": "string"},
                                                {"name": "收入", "role": "metric", "data_type": "decimal"}]})
    chart = recommend("收入对比", result)
    assert chart.type == "bar" and chart.rows[0][1] == 12.5
    assert result["rows"][0][1] == "12.500"
