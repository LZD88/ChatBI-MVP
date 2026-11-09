from chatbi.application.result_localizer import chinese_column_label, localize_columns


def test_known_business_columns_use_clear_chinese_labels() -> None:
    assert localize_columns(["warehouse_id", "warehouse_name", "available_inventory"]) == [
        "仓库ID", "仓库名称", "可用库存",
    ]
    assert localize_columns(["product_line", "gross_profit_cny"]) == ["产品线", "人民币毛利"]


def test_existing_chinese_alias_is_preserved() -> None:
    assert chinese_column_label("预算执行率") == "预算执行率"


def test_unknown_snake_case_uses_deterministic_chinese_token_fallback() -> None:
    assert chinese_column_label("average_supplier_rating") == "平均供应商评分"


def test_duplicate_labels_remain_unique() -> None:
    assert localize_columns(["order_count", "count"]) == ["订单数量", "数量"]
    assert localize_columns(["revenue", "revenue"]) == ["收入", "收入（2）"]
