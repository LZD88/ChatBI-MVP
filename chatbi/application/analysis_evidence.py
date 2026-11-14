"""Compute transparent descriptive statistics from all returned rows."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from chatbi.domain.complex_analysis import StepExecutionResult


def numeric(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def profile(step: StepExecutionResult) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for index, column in enumerate(step.columns):
        values = [(row_index, numeric(row[index])) for row_index, row in enumerate(step.rows)]
        valid = [(row_index, value) for row_index, value in values if value is not None]
        if not valid:
            continue
        lowest = min(valid, key=lambda item: item[1])
        highest = max(valid, key=lambda item: item[1])
        metrics[column] = {
            "count": len(valid), "null_or_non_numeric_count": len(values) - len(valid),
            "min": {"row_index": lowest[0], "value": str(lowest[1])},
            "max": {"row_index": highest[0], "value": str(highest[1])},
            "mean": str(sum(value for _, value in valid) / Decimal(len(valid))),
            "scope": "当前返回行；不是数据库全量或加权均值",
        }
    return {
        "row_count": step.row_count, "complete": not step.truncated,
        "metrics": metrics,
    }


def evidence_rows(step: StepExecutionResult, limit: int) -> list[int]:
    """Show extrema as well as a stable spread of rows, preserving original indexes."""
    if len(step.rows) <= limit:
        return list(range(len(step.rows)))
    selected: list[int] = []
    for metric in profile(step)["metrics"].values():
        selected.extend([metric["min"]["row_index"], metric["max"]["row_index"]])
    selected = list(dict.fromkeys(selected))[:limit]
    for index in range(limit):
        row_index = round(index * (len(step.rows) - 1) / max(limit - 1, 1))
        if row_index not in selected:
            selected.append(row_index)
        if len(selected) >= limit:
            break
    return sorted(selected)
