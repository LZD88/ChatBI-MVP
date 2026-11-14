"""Deterministic chart specifications built only from successful query results."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from chatbi.application.analysis_evidence import numeric
from chatbi.domain.models import QueryResult


class VisualizationSpec(BaseModel):
    type: Literal["kpi", "line", "bar", "stacked_bar", "pie", "waterfall"]
    title: str
    source_step_id: str | None = None
    columns: list[str]
    rows: list[list[Any]]
    dimension: str | None = None
    metrics: list[str]
    series_dimension: str | None = None
    total_rows: int
    sampled: bool = False
    truncated: bool = False
    note: str
    # Only identifiers and values; no JS, HTML, ECharts options or model code.
    version: int = Field(default=1, frozen=True)


def build_visualizations(result: QueryResult) -> list[VisualizationSpec]:
    if result.step_results:
        sources = [step for step in result.step_results if step.get("status") == "succeeded"]
    else:
        sources = [{"columns": list(result.columns), "rows": [list(row) for row in result.rows],
                    "truncated": result.truncated}]
    charts: list[VisualizationSpec] = []
    for source in sources[:8]:
        chart = recommend(result.question, source)
        if chart:
            charts.append(chart)
    return charts


def recommend(question: str, source: dict[str, Any]) -> VisualizationSpec | None:
    columns = source.get("columns", [])
    rows = [list(row) for row in source.get("rows", [])]
    if not rows or not columns or len(columns) != len(set(columns)) or any(len(row) != len(columns) for row in rows):
        return None
    contract = {col["name"]: col for col in (source.get("result_artifact") or {}).get("columns", [])}
    # Pydantic's JSON mode serializes database Decimals as strings. Convert
    # only fields explicitly declared numeric in the successful step contract.
    for index, column in enumerate(columns):
        if contract.get(column, {}).get("role") == "metric" and contract[column].get("data_type") in {"integer", "decimal", "percentage"}:
            for row in rows:
                if isinstance(row[index], str):
                    try:
                        row[index] = Decimal(row[index])
                    except Exception:
                        return None
    metrics: list[str] = []
    dimensions: list[str] = []
    for index, column in enumerate(columns):
        values = [row[index] for row in rows if row[index] is not None]
        role = contract.get(column, {}).get("role")
        identifier = bool(re.search(r"(?:编号|编码|代码|ID$|_id$|年份|年度|月份|季度|日期|^(?:年|月|year|month|quarter|day)$|_(?:year|month|date)$)", column, re.I))
        if values and all(numeric(value) is not None for value in values) and role != "dimension" and not identifier:
            # Avoid silently rounding large identifiers/amounts into JS doubles.
            if any(abs(Decimal(str(value))) > 2**53 - 1 for value in values):
                continue
            metrics.append(column)
        elif role != "metric":
            dimensions.append(column)
    if not metrics:
        return None
    if any(col in contract and contract[col].get("role") == "metric" and col not in metrics for col in columns):
        return None  # Do not silently discard a malformed declared metric.
    metrics = metrics[:4]
    dimension = dimensions[0] if dimensions else None
    series_dimension = None
    kind: Literal["kpi", "line", "bar", "stacked_bar", "pie", "waterfall"] = "bar"
    if len(rows) == 1:
        kind = "kpi"
    elif dimension is None:
        return None
    else:
        di = columns.index(dimension)
        labels = [str(row[di]) for row in rows]
        if any(row[di] is None for row in rows):
            return None
        unique = len(set(labels)) == len(labels)
        time_axis = all(isinstance(row[di], (date, datetime)) or re.fullmatch(r"\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?(?:[ T].*)?", str(row[di])) for row in rows)
        if not unique:
            # Stack only a single additive metric split by two dimensions.
            if len(dimensions) != 2 or len(metrics) != 1 or not _additive(metrics[0]):
                return None
            series_dimension = dimensions[1]
            si = columns.index(series_dimension)
            if any(row[si] is None for row in rows) or len({(str(row[di]), str(row[si])) for row in rows}) != len(rows):
                return None
            if len({str(row[si]) for row in rows}) > 8:
                return None
            kind = "stacked_bar"
        elif len(dimensions) > 1:
            return None  # Do not flatten mixed grains into a misleading chart.
        elif time_axis:
            kind = "line"
            rows = sorted(rows, key=lambda row: tuple(int(part) for part in re.findall(r"\d+", str(row[di]))))
        elif len(metrics) == 1 and _additive(metrics[0]):
            numbers = [numeric(row[columns.index(metrics[0])]) for row in rows]
            if any(word in question for word in ("占比", "构成", "饼图")) and 2 <= len(rows) <= 8 and all(v is not None and v >= 0 for v in numbers) and sum(v for v in numbers if v is not None) > 0:
                kind = "pie"
            elif "瀑布" in question and any(word in metrics[0] for word in ("变动", "增减", "贡献")) and all(v is not None for v in numbers) and len(rows) <= 30:
                kind = "waterfall"
    selected = rows[:100]
    used = [col for col in (dimension, series_dimension, *metrics) if col is not None]
    converted = []
    for row in selected:
        converted.append([
            float(row[columns.index(col)]) if col in metrics and row[columns.index(col)] is not None
            else (str(row[columns.index(col)]) if row[columns.index(col)] is not None else None)
            for col in used
        ])
    suffix = "仅展示前 100 行；" if len(rows) > 100 else ""
    suffix += "查询结果已截断；" if source.get("truncated") else ""
    return VisualizationSpec(
        type=kind, title=source.get("step_name") or "查询结果可视化",
        source_step_id=source.get("step_id"), columns=used, rows=converted,
        dimension=dimension, metrics=metrics, series_dimension=series_dimension,
        total_rows=len(rows), sampled=len(rows) > 100, truncated=bool(source.get("truncated")),
        note=suffix + "图表基于返回数据，不额外聚合；各指标使用独立数值轴。" + ("瀑布从零累计变动项，不代表期末余额。" if kind == "waterfall" else ""),
    )


def _additive(column: str) -> bool:
    return not any(term in column.lower() for term in ("率", "占比", "均", "单价", "ratio", "rate", "percent", "%"))
