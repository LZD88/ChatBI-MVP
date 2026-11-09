from __future__ import annotations

from types import SimpleNamespace

import pytest

from chatbi.infrastructure.intent import (
    FallbackIntentClassifier,
    HeuristicIntentClassifier,
    QwenIntentClassifier,
)


def test_qwen_response_parser_accepts_strict_json() -> None:
    result = QwenIntentClassifier.parse_response(
        '{"query_type":"complex","confidence":0.93,"reason":"涉及多指标趋势对比"}'
    )
    assert result.query_type == "complex"
    assert result.confidence == 0.93
    assert result.source == "qwen"


def test_intent_prompt_explicitly_allows_simple_joins() -> None:
    from chatbi.infrastructure.intent import INTENT_SYSTEM_PROMPT

    assert "联表查询也可以是 simple" in INTENT_SYSTEM_PROMPT
    assert "不要仅因出现 JOIN" in INTENT_SYSTEM_PROMPT


@pytest.mark.parametrize(
    "content",
    [None, "", "not json", '{"query_type":"unknown","confidence":1,"reason":"x"}',
     '{"query_type":"simple","confidence":2,"reason":"x"}'],
)
def test_qwen_response_parser_rejects_invalid_output(content: str | None) -> None:
    with pytest.raises(ValueError):
        QwenIntentClassifier.parse_response(content)


def test_classifier_calls_qwen_with_deterministic_settings() -> None:
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content='{"query_type":"simple","confidence":0.88,"reason":"单一聚合"}')
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    settings = SimpleNamespace(intent_api_key="unused", intent_base_url="unused", intent_timeout_seconds=10,
                               intent_model="qwen-plus")
    result = QwenIntentClassifier(settings=settings, client=client).classify("订单数量")
    assert result.query_type == "simple"
    assert captured["model"] == "qwen-plus"
    assert captured["temperature"] == 0


def test_qwen_failure_falls_back_to_local_classifier() -> None:
    class Broken:
        def classify(self, question: str):
            raise RuntimeError("remote unavailable")

    classifier = FallbackIntentClassifier(Broken(), HeuristicIntentClassifier())
    result = classifier.classify("比较收入和毛利趋势")
    assert result.query_type == "complex"
    assert result.source == "fallback"
    assert classifier.last_error == "RuntimeError"


def test_join_word_alone_does_not_make_fallback_complex() -> None:
    result = HeuristicIntentClassifier().classify("关联供应商表，按供应商统计采购金额")
    assert result.query_type == "simple"
