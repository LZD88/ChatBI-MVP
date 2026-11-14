"""Safe reflection diagnostics and resumable request-local circuit state."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
import re
from typing import Any

import sqlglot
from sqlglot import exp

from chatbi.domain.errors import ChatBIError, ErrorCode
from database import QueryExecutionError


class AnalysisSuspended(ChatBIError):
    def __init__(self, state: dict[str, Any], public: dict[str, Any]) -> None:
        super().__init__(ErrorCode.ANALYSIS_SUSPENDED)
        self.state, self.public = state, public


def reflection(error: Exception, sql: str, attempt: int) -> dict[str, Any]:
    """Expose controlled diagnostic categories, never vendor text or SQL values."""
    code, message, retryable = "step_failed", "步骤发生异常，请核对输出契约。", False
    if isinstance(error, ChatBIError):
        code = error.code.value
        message = {
            ErrorCode.SQL_REJECTED: "只读安全或计划范围校验未通过；只使用授权表字段，禁止写入、危险函数和多语句。",
            ErrorCode.MODEL_OUTPUT_INVALID: "模型没有返回完整有效的只读 SQL。",
            ErrorCode.MODEL_UNAVAILABLE: "模型服务调用失败，可在剩余预算内重试。",
        }.get(error.code, "步骤校验未通过，请遵循计划和数据权限。")
        retryable = error.code in {ErrorCode.SQL_REJECTED, ErrorCode.MODEL_OUTPUT_INVALID, ErrorCode.MODEL_UNAVAILABLE}
    elif isinstance(error, QueryExecutionError):
        code = error.error_type
        messages = {
            "sql_syntax": "SQL 语法错误，核对字段、聚合和别名。",
            "query_timeout": "查询超时，请减少扫描和无效关联，保持原问题范围与指标口径。",
            "connection_error": "数据库暂时不可连接。",
            "permission_denied": "数据库拒绝访问，禁止通过更换账号或访问系统表绕过权限。",
            "execution_error": "数据库执行失败，请核对列与关联方式。",
        }
        message = messages.get(code, "数据库执行失败，请核对查询结构。")
        errno = error.metadata.get("error_code")
        if errno in {1054, 1146, 1055, 1064}:
            message = {1054: "引用了不存在的列，请严格使用当前 Schema 和输入表字段。",
                       1146: "引用了不存在的表，请检查输入表与 CTE 定义。",
                       1055: "聚合粒度错误，非聚合字段必须符合分组规则。",
                       1064: "SQL 语法错误，请生成完整 MySQL 8 查询。"}[errno]
        retryable = code not in {"permission_denied", "connection_error"}
    elif isinstance(error, ValueError):
        code, retryable = "step_contract_invalid", True
        # Our controlled contract exceptions can name columns but contain no
        # database cells. Do not include arbitrary ValueError vendor payloads.
        text = str(error)
        if re.match(r"^(输出|整数字段|依赖|上游|模型不得|SQL 必须|输入绑定)", text):
            message = text[:240]
        else:
            message = "SQL 结构或输出契约不符合要求，请核对列名、类型与输入绑定。"
    structure = ""
    if sql:
        try:
            query = sqlglot.parse_one(sql, read="mysql")
            for node in list(query.find_all(exp.Literal)):
                node.replace(exp.Literal.string("__value__") if node.is_string else exp.Literal.number("0"))
            for expression in query.walk():
                expression.comments = None
            structure = query.sql(dialect="mysql")[:1800]
        except Exception:
            structure = "上次 SQL 无法解析，已省略原文。"
    return {"attempt": attempt, "code": code, "message": message,
            "retryable": retryable, "sql_structure": structure}


def encode_state(state: dict[str, Any]) -> str:
    def default(value: Any) -> Any:
        if isinstance(value, (Decimal, datetime, date)):
            return {"__chatbi_type__": type(value).__name__, "value": str(value)}
        raise TypeError("unsupported_checkpoint_type")
    return json.dumps(state, ensure_ascii=False, default=default)


def decode_state(payload: str) -> dict[str, Any]:
    def hook(value: dict[str, Any]) -> Any:
        if set(value) == {"__chatbi_type__", "value"}:
            kind, text = value["__chatbi_type__"], value["value"]
            if kind == "Decimal":
                return Decimal(text)
            if kind == "date":
                return date.fromisoformat(text)
            if kind == "datetime":
                return datetime.fromisoformat(text)
        return value
    return json.loads(payload, object_hook=hook)
