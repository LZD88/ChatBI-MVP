from types import SimpleNamespace

import pytest
from openai import APITimeoutError

from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.settings import Settings
from llm_client import LLMClient


class FakeCompletions:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self.content = content
        self.error = error

    def create(self, **_: object):
        if self.error:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


def client_with(content: str | None = None, error: Exception | None = None) -> LLMClient:
    fake = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(content, error)))
    return LLMClient(Settings(_env_file=None), client=fake)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("```sql\nSELECT order_id FROM sales_orders\n```", "SELECT order_id FROM sales_orders"),
        ("下面是查询： SELECT COUNT(*) FROM sales_orders;", "SELECT COUNT(*) FROM sales_orders"),
        ("WITH x AS (SELECT order_id FROM sales_orders) SELECT * FROM x", "WITH x AS (SELECT order_id FROM sales_orders) SELECT * FROM x"),
    ],
)
def test_extracts_one_sql_statement(content: str, expected: str) -> None:
    assert client_with(content).generate_sql("system", "prompt") == expected


@pytest.mark.parametrize("content", [None, "", "没有 SQL", "SELECT 1; SELECT 2"])
def test_rejects_invalid_model_output(content: str | None) -> None:
    with pytest.raises(ChatBIError) as exc:
        client_with(content).generate_sql("system", "prompt")
    assert exc.value.code == ErrorCode.MODEL_OUTPUT_INVALID


def test_maps_timeout_to_stable_model_error() -> None:
    timeout = APITimeoutError(request=SimpleNamespace())
    with pytest.raises(ChatBIError) as exc:
        client_with(error=timeout).generate_sql("system", "prompt")
    assert exc.value.code == ErrorCode.MODEL_UNAVAILABLE
