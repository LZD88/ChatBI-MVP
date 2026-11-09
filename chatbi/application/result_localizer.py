"""Deterministic Chinese labels for user-visible query result columns."""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable


COLUMN_LABELS: dict[str, str] = {
    "warehouse_id": "仓库ID",
    "warehouse_name": "仓库名称",
    "available_inventory": "可用库存",
    "product_id": "产品ID",
    "product_name": "产品名称",
    "product_line": "产品线",
    "customer_id": "客户ID",
    "customer_name": "客户名称",
    "customer_type": "客户类型",
    "supplier_id": "供应商ID",
    "supplier_name": "供应商名称",
    "supplier_category": "供应商类别",
    "region": "地区",
    "currency": "币种",
    "order_id": "订单ID",
    "order_no": "订单编号",
    "order_count": "订单数量",
    "completed_order_count": "完成订单数",
    "purchase_order_id": "采购订单ID",
    "purchase_order_no": "采购订单编号",
    "purchase_amount": "采购金额",
    "purchase_quantity": "采购数量",
    "received_quantity": "已收货数量",
    "receipt_rate": "采购收货率",
    "revenue": "收入",
    "revenue_cny": "人民币收入",
    "sales_amount": "销售额",
    "sales_cost": "销售成本",
    "gross_profit": "毛利",
    "gross_profit_cny": "人民币毛利",
    "gross_margin": "毛利率",
    "gross_margin_rate": "毛利率",
    "profit": "利润",
    "profit_margin": "利润率",
    "period_expense": "期间费用",
    "total_expense": "期间费用",
    "quantity": "数量",
    "sales_quantity": "销售数量",
    "inventory_quantity": "库存数量",
    "inventory_value": "库存金额",
    "reserved_inventory": "预留库存",
    "production_yield_rate": "生产良率",
    "planned_quantity": "计划生产数量",
    "actual_output_quantity": "实际产出数量",
    "good_quantity": "良品数量",
    "defect_quantity": "不良品数量",
    "pass_rate": "检验合格率",
    "inspection_pass_rate": "检验合格率",
    "defect_rate": "检验缺陷率",
    "maintenance_count": "设备维护次数",
    "maintenance_cost": "设备维护成本",
    "total_maintenance_cost": "设备维护成本",
    "downtime_hours": "设备停机时长",
    "shipment_count": "发运单数",
    "shipped_quantity": "发运数量",
    "freight_cost": "运费",
    "payment_amount": "付款金额",
    "collection_amount": "客户回款金额",
    "collection_rate": "客户回款率",
    "budget_amount": "预算金额",
    "actual_amount": "实际发生金额",
    "budget_variance": "预算差异",
    "budget_execution_rate": "预算执行率",
    "employee_count": "员工人数",
    "active_employee_count": "在职员工数",
    "department_name": "部门名称",
    "cost_center_name": "成本中心名称",
    "equipment_name": "设备名称",
    "equipment_type": "设备类型",
    "carrier_name": "承运商",
    "month": "月份",
    "year": "年份",
    "quarter": "季度",
    "date": "日期",
}

TOKEN_LABELS: dict[str, str] = {
    "id": "ID", "name": "名称", "count": "数量", "total": "总计", "avg": "平均",
    "average": "平均", "amount": "金额", "quantity": "数量", "rate": "率", "ratio": "占比",
    "cost": "成本", "profit": "利润", "margin": "利润率", "revenue": "收入", "sales": "销售",
    "purchase": "采购", "inventory": "库存", "available": "可用", "reserved": "预留",
    "warehouse": "仓库", "product": "产品", "customer": "客户", "supplier": "供应商",
    "order": "订单", "payment": "付款", "budget": "预算", "actual": "实际", "variance": "差异",
    "execution": "执行", "production": "生产", "output": "产出", "good": "良品", "defect": "缺陷",
    "inspection": "检验", "pass": "合格", "maintenance": "维护", "equipment": "设备",
    "downtime": "停机时长", "shipment": "发运", "shipped": "发运", "freight": "运费",
    "employee": "员工", "department": "部门", "region": "地区", "currency": "币种",
    "rating": "评分",
    "line": "线", "type": "类型", "status": "状态", "date": "日期", "month": "月份",
    "year": "年份", "quarter": "季度", "cny": "人民币", "received": "已收货",
    "completed": "完成", "planned": "计划", "gross": "毛", "net": "净",
}


def chinese_column_label(column: str) -> str:
    """Translate known aliases and deterministically localize unknown snake_case names."""
    original = str(column).strip()
    if not original or re.search(r"[\u3400-\u9fff]", original):
        return original
    normalized = original.lower().replace(" ", "_")
    if normalized in COLUMN_LABELS:
        return COLUMN_LABELS[normalized]
    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
    translated = [TOKEN_LABELS.get(token, token.upper()) for token in tokens]
    return "".join(translated) or original


def localize_columns(columns: Iterable[str]) -> list[str]:
    """Return unique Chinese labels while preserving the database column order."""
    labels = [chinese_column_label(column) for column in columns]
    counts: Counter[str] = Counter()
    unique: list[str] = []
    for label in labels:
        counts[label] += 1
        unique.append(label if counts[label] == 1 else f"{label}（{counts[label]}）")
    return unique
