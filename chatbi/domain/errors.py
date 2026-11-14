"""Stable domain errors exposed by the application and API layers."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INPUT_INVALID = "input_invalid"
    INPUT_TOO_LONG = "input_too_long"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_OUTPUT_INVALID = "model_output_invalid"
    SCHEMA_UNAVAILABLE = "schema_unavailable"
    SQL_REJECTED = "sql_rejected"
    DATABASE_UNAVAILABLE = "database_unavailable"
    DATABASE_TIMEOUT = "database_timeout"
    DATABASE_QUERY_FAILED = "database_query_failed"
    INTERNAL_ERROR = "internal_error"
    ANALYSIS_SUSPENDED = "analysis_suspended"


DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INPUT_INVALID: "请输入有效的业务问题。",
    ErrorCode.INPUT_TOO_LONG: "问题过长，请缩短后重试。",
    ErrorCode.MODEL_UNAVAILABLE: "模型服务暂时不可用，请稍后重试。",
    ErrorCode.MODEL_OUTPUT_INVALID: "模型没有生成可用的 SQL，请换一种方式描述问题。",
    ErrorCode.SCHEMA_UNAVAILABLE: "数据库结构信息暂时不可用。",
    ErrorCode.SQL_REJECTED: "生成的查询未通过只读安全检查，因此没有执行。",
    ErrorCode.DATABASE_UNAVAILABLE: "演示数据库暂时不可用。",
    ErrorCode.DATABASE_TIMEOUT: "查询执行超时，请缩小查询范围后重试。",
    ErrorCode.DATABASE_QUERY_FAILED: "查询执行失败，请调整问题后重试。",
    ErrorCode.INTERNAL_ERROR: "系统处理失败，请稍后重试。",
    ErrorCode.ANALYSIS_SUSPENDED: "核心分析步骤失败，已保存进度并挂起，等待补充条件或重试。",
}


class ChatBIError(RuntimeError):
    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or DEFAULT_MESSAGES[code])
        self.code = code
        self.public_message = message or DEFAULT_MESSAGES[code]
        self.details = details or {}
