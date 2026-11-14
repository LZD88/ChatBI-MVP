"""Materialize request-local results as read-only SQL AST input relations."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import math
from typing import Any

import sqlglot
from sqlglot import exp

from chatbi.domain.complex_analysis import PlanStep, StepExecutionResult, StepStatus
from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.settings import Settings


def validate_output(step: PlanStep, columns: list[str], rows: list[list[Any]]) -> None:
    if len(set(columns)) != len(columns) or any(len(row) != len(columns) for row in rows):
        raise ValueError("输出数据列不唯一或行宽不一致")
    for expected in step.expected_columns:
        if expected.name not in columns:
            raise ValueError(f"输出缺少契约字段：{expected.name}")
        for row in rows:
            value = row[columns.index(expected.name)]
            if value is None:
                continue
            if expected.data_type in {"integer", "decimal", "percentage"}:
                if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
                    raise ValueError(f"输出字段类型与契约不一致：{expected.name}")
                if not Decimal(str(value)).is_finite():
                    raise ValueError("输出包含非有限数值")
                if expected.data_type == "integer" and Decimal(str(value)) % 1:
                    raise ValueError("整数字段包含非整数")
            elif expected.data_type == "string" and not isinstance(value, str):
                raise ValueError(f"输出字段类型与契约不一致：{expected.name}")
            elif expected.data_type in {"date", "datetime"}:
                if not isinstance(value, (date, datetime)):
                    raise ValueError(f"输出字段类型与契约不一致：{expected.name}")


def bind_results(
    sql: str, step: PlanStep, results: dict[str, StepExecutionResult], settings: Settings,
) -> tuple[str, list[str]]:
    """Inject literal ASTs built from real cells, never string-formatted SQL.

    The model sees relation schemas only. Values cannot alter the SQL structure,
    and every expanded query must still pass the SQL security boundary.
    """
    if not step.input_bindings:
        return sql, []
    statements = sqlglot.parse(sql, read="mysql")
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ChatBIError(ErrorCode.SQL_REJECTED)
    query = statements[0]
    existing = {cte.alias_or_name.lower() for cte in query.find_all(exp.CTE)}
    ctes: list[exp.CTE] = []
    consumed: list[str] = []
    total_cells = 0
    for binding in step.input_bindings:
        source = results.get(binding.source_step_id)
        if source is None or source.status is not StepStatus.SUCCEEDED or source.result_artifact is None:
            raise ValueError("上游步骤没有可用结果产物")
        if source.truncated or not source.result_artifact.complete:
            raise ValueError("上游结果被截断，不能作为完整依赖输入")
        if len(source.rows) > settings.complex_dependency_max_rows:
            raise ValueError("上游结果超过依赖行数上限，请先聚合或筛选")
        total_cells += len(source.rows) * len(binding.columns)
        if total_cells > settings.complex_dependency_max_cells:
            raise ValueError("依赖数据超过单元格上限")
        if binding.name in existing:
            raise ValueError("模型不得覆盖系统输入表")
        references = [table for table in query.find_all(exp.Table) if table.name.lower() == binding.name]
        if not references or any(table.db or table.catalog for table in references):
            raise ValueError("SQL 必须引用已声明的上游输入表")
        contract = {item.name: item for item in source.result_artifact.columns}
        if set(binding.columns) - set(contract) or set(binding.columns) - set(source.columns):
            raise ValueError("依赖字段未在上游输出契约中声明")
        indexes = [source.columns.index(column) for column in binding.columns]
        relation: exp.Query | None = None
        rows = source.rows or [[None] * len(source.columns)]
        for row in rows:
            expressions = [
                exp.alias_(_literal(row[index]), column, quoted=True)
                for index, column in zip(indexes, binding.columns)
            ]
            record = exp.select(*expressions)
            if not source.rows:
                record = record.where(exp.false())
            relation = record if relation is None else exp.Union(this=relation, expression=record, distinct=False)
        ctes.append(exp.CTE(this=relation, alias=exp.TableAlias(this=exp.to_identifier(binding.name))))
        consumed.append(source.result_artifact.artifact_id)
    current = query.args.get("with_")
    query.set("with_", exp.With(
        expressions=[*ctes, *(current.expressions if current else [])],
        recursive=bool(current and current.args.get("recursive")),
    ))
    return query.sql(dialect="mysql"), list(dict.fromkeys(consumed))


def _literal(value: Any) -> exp.Expression:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.true() if value else exp.false()
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and not math.isfinite(value) or not Decimal(str(value)).is_finite():
            raise ValueError("依赖结果含非有限数值")
        return exp.Literal.number(str(value))
    if isinstance(value, datetime):
        return exp.cast(exp.Literal.string(value.isoformat(sep=" ")), "DATETIME")
    if isinstance(value, date):
        return exp.cast(exp.Literal.string(value.isoformat()), "DATE")
    if isinstance(value, str) and len(value) <= 2000:
        return exp.Literal.string(value)
    raise ValueError("依赖值类型不支持或文本过长")
