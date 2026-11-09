import json
import logging

from chatbi.domain.errors import ErrorCode
from chatbi.logging import JsonFormatter, request_context


def test_error_codes_cover_pipeline_failures() -> None:
    assert ErrorCode.INPUT_INVALID == "input_invalid"
    assert ErrorCode.MODEL_UNAVAILABLE == "model_unavailable"
    assert ErrorCode.SCHEMA_UNAVAILABLE == "schema_unavailable"
    assert ErrorCode.SQL_REJECTED == "sql_rejected"
    assert ErrorCode.DATABASE_TIMEOUT == "database_timeout"
    assert ErrorCode.INTERNAL_ERROR == "internal_error"


def test_json_log_contains_request_id_and_redacts_secrets() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        "chatbi.test",
        logging.INFO,
        __file__,
        1,
        "api_key=sk-abcdefgh password=hunter2 dsn=mysql://user:secret@localhost/db",
        (),
        None,
    )

    with request_context("request-123"):
        payload = json.loads(formatter.format(record))

    assert payload["request_id"] == "request-123"
    assert "sk-abcdefgh" not in payload["message"]
    assert "hunter2" not in payload["message"]
    assert "user:secret@" not in payload["message"]
    assert payload["message"].count("***") >= 3
