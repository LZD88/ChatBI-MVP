"""Drill into a stored successful query using AST literals, never browser SQL."""
from __future__ import annotations

from typing import Any
import sqlglot
from sqlglot import exp

from chatbi.domain.security import DEFAULT_SCHEMA
from chatbi.application.result_localizer import localize_columns


def source_result(response: dict[str, Any], step_id: str | None) -> dict[str, Any]:
    if step_id and step_id != "main":
        source = next((row for row in response.get("step_results", []) if row["step_id"] == step_id and row["status"] == "succeeded"), None)
        if not source:
            raise ValueError("指定步骤没有成功结果。")
        return source
    return response


def build_detail_sql(source: dict[str, Any], dimension: str, value: Any) -> str:
    if dimension not in source["columns"]:
        raise ValueError("下钻维度不在原始结果中。")
    index = source["columns"].index(dimension)
    if not any(str(row[index]) == str(value) for row in source["rows"]):
        raise ValueError("下钻对象必须来自原查询结果。")
    tree = sqlglot.parse_one(source["sql"], read="mysql")
    if not isinstance(tree, exp.Select) or any(tree.find_all(exp.Subquery)):
        raise ValueError("当前结果含跨步骤或嵌套计算，请通过证据行查看，或继续追问明细。")
    projections = tree.expressions
    if len(projections) != len(source["columns"]):
        raise ValueError("无法确定维度与输出字段的对应关系。")
    expression = projections[index]
    expression = expression.this if isinstance(expression, exp.Alias) else expression
    if not isinstance(expression, exp.Column):
        raise ValueError("只支持按原始业务维度下钻，不能把计算指标作为筛选维度。")
    if tree.args.get("with_"):
        root_tables = {table.alias_or_name: table.name for table in tree.find_all(exp.Table)
                       if table.find_ancestor(exp.Select) is tree}
        dimension_table = root_tables.get(expression.table)
        # Support a common two-level rollup: aggregate fact rows in one CTE,
        # then roll up by a directly joined dimension. Intermediate HAVING,
        # extra filters, unions or additional CTEs need explicit lineage and
        # are refused instead of silently widening their business scope.
        selects = list(tree.find_all(exp.Select))
        ctes = list(tree.find_all(exp.CTE))
        root_joins = tree.args.get("joins") or []
        if (len(selects) != 2 or len(ctes) != 1 or tree.args.get("where") or tree.args.get("having")
                or dimension_table not in DEFAULT_SCHEMA or not dimension_table.startswith("dim_")
                or len(root_tables) != 2 or set(root_tables.values()) != {ctes[0].alias_or_name, dimension_table}
                or len(root_joins) != 1):
            raise ValueError("当前跨步骤计算不支持自动下钻，请点击报告证据或继续追问业务明细。")
        on = root_joins[0].args.get("on")
        if (not isinstance(on, exp.EQ) or not isinstance(on.this, exp.Column) or not isinstance(on.expression, exp.Column)
                or on.this.name != on.expression.name or not on.this.name.endswith("_id")):
            raise ValueError("中间关联包含额外条件，不能安全展开为明细。")
        leaf = ctes[0].this
        leaf_tables = list(leaf.find_all(exp.Table))
        if (not isinstance(leaf, exp.Select) or leaf.args.get("having") or leaf.args.get("limit")
                or leaf.args.get("distinct") or any(table.name not in DEFAULT_SCHEMA for table in leaf_tables)):
            raise ValueError("无法证明下钻仍保留中间计算条件，请查看原始证据。")
        target = next((table for table in leaf_tables if table.name == dimension_table), None)
        if target is None:
            raise ValueError("明细来源中没有该维度，请继续追问带此维度的明细。")
        key_projection = next((node for node in leaf.expressions if node.alias_or_name == on.this.name), None)
        key_value = key_projection.this if isinstance(key_projection, exp.Alias) else key_projection
        if not isinstance(key_value, exp.Column) or key_value.name != on.this.name:
            raise ValueError("中间关联键经过计算，不能展开为原始明细。")
        expression = exp.column(expression.name, table=target.alias_or_name)
        tree = leaf.copy()
    predicate = exp.Is(this=expression.copy(), expression=exp.Null()) if value is None else exp.EQ(this=expression.copy(), expression=exp.convert(value))
    tree = tree.where(predicate, append=True)
    tables = list(tree.find_all(exp.Table))
    fields = [exp.alias_(exp.column(field, table=table.alias_or_name), f"{table.name}.{field}", quoted=True)
              for table in tables for field in sorted(DEFAULT_SCHEMA.get(table.name, []))]
    if not fields:
        raise ValueError("当前查询没有可下钻的业务字段。")
    tree.set("expressions", fields[:60])
    for key in ("group", "having", "order", "limit", "offset", "distinct"):
        tree.set(key, None)
    return tree.sql(dialect="mysql")


def evidence_rows(response: dict[str, Any], step_id: str, row_index: int, column: str) -> dict[str, Any]:
    source = source_result(response, step_id)
    if row_index < 0 or row_index >= len(source["rows"]) or column not in source["columns"]:
        raise ValueError("证据引用超出保存的结果范围。")
    return {"columns": source["columns"], "rows": [source["rows"][row_index]], "row_index": row_index,
            "step_id": step_id, "column": column, "sql": source["sql"],
            "note": "原报告引用的历史证据行；未重新执行数据库查询。"}
