"""Regression coverage for greetings during continuous business analysis."""
import json
from threading import Event
from time import monotonic

import pytest
from fastapi.testclient import TestClient

from chatbi.api.app import create_app
from chatbi.application.failure_control import AnalysisSuspended
from chatbi.application.progress import (
    QueryControl, analysis_options, query_control, query_options,
)
from chatbi.domain.models import QueryIntent
from chatbi.infrastructure.intent import (
    FallbackIntentClassifier, HeuristicIntentClassifier, QwenIntentClassifier,
    obvious_non_query,
)
from tests.test_api import settings
from tests.test_query_jobs import wait_until
from tests.test_query_service import FakeIntent, make_service


class ForbiddenDependency:
    def __getattr__(self, name):
        raise AssertionError(f"Non-query must not call {name}")


def isolated_service(intent=None):
    service = make_service(intent=intent)
    for name in ("llm", "schema_provider", "database", "simple_semantic",
                 "complex_semantic", "plan_execute", "validator", "indicators", "security"):
        setattr(service, name, ForbiddenDependency())
    return service


@pytest.mark.parametrize("question", ["你好", "您好！", "谢谢", "你能做什么", "hello"])
def test_greetings_short_circuit_every_business_dependency_with_complex_history(question):
    intent = FakeIntent("complex")
    service = isolated_service(intent)
    events = []
    context = {"turns": [{"question": "分步骤分析收入和毛利趋势"}, {"question": question}]}
    control = QueryControl(Event(), monotonic() + 10, lambda stage, _: events.append(stage), options={"review_plan": True})
    with query_control(control), analysis_options({"analysis_context": context, "clarify_before_query": True}):
        result = service.query("历史分步骤分析收入，当前追问：" + question)
    assert result.query_type == "non_query"
    assert result.question == question and result.message
    assert not result.sql and not result.rows and result.plan is None
    assert intent.calls == 0
    assert events == ["intent", "conversation_ready"]


def test_model_detected_non_query_also_skips_business_dependencies_and_clarification():
    # Contains an ambiguity trigger, but the request is ordinary conversation.
    intent = FakeIntent("non_query")
    with analysis_options({"clarify_before_query": True}):
        result = isolated_service(intent).query("最近你能帮我做什么？")
    assert result.query_type == "non_query" and intent.calls == 1


def test_greeting_prefix_does_not_hide_a_real_query():
    question = "你好，按产品线统计收入"
    assert obvious_non_query(question) is None
    assert make_service().query(question).query_type == "simple"


def test_only_current_turn_controls_multistage_guard():
    class Primary:
        def classify(self, question):
            return QueryIntent("simple", .9, "单次聚合", "qwen")

    payload = json.dumps({"current_question": "只看华东", "analysis_context": ["先汇总收入再分析毛利，分步骤执行"]})
    assert FallbackIntentClassifier(Primary()).classify(payload).query_type == "simple"
    assert HeuristicIntentClassifier().classify(payload).query_type == "simple"
    assert FallbackIntentClassifier(Primary()).classify("先汇总收入再分析毛利").query_type == "complex"


def test_parser_and_fallback_support_non_query():
    assert QwenIntentClassifier.parse_response('{"query_type":"non_query","confidence":0.99,"reason":"闲聊"}').query_type == "non_query"

    class Broken:
        def classify(self, question):
            raise RuntimeError("unavailable")

    result = FallbackIntentClassifier(Broken()).classify("今天天气如何？")
    assert result.query_type == "non_query" and result.source == "fallback"


def test_structured_context_reaches_classifier_but_does_not_replace_sql_context():
    captured = []

    class Classifier:
        def classify(self, question):
            captured.append(json.loads(question))
            return QueryIntent("simple", .9, "单一目标", "qwen")

    service = make_service(intent=Classifier())
    with analysis_options({"analysis_context": {"turns": [{"question": "分步骤分析收入"}, {"question": "只看华东"}]}}):
        assert service.query("分步骤分析收入；当前追问：只看华东").query_type == "simple"
    assert captured == [{"current_question": "只看华东", "analysis_context": ["分步骤分析收入"]}]


def test_business_ambiguity_still_suspends_after_intent():
    service = isolated_service(FakeIntent("simple"))
    with analysis_options({"clarify_before_query": True}), pytest.raises(AnalysisSuspended):
        service.query("最近利润最好的是哪些产品线")


def test_options_override_is_scoped_and_takes_precedence_inside_job():
    control = QueryControl(Event(), monotonic() + 10, lambda *_: None, options={"old": True})
    with query_control(control):
        with analysis_options({"current": True}):
            assert query_options() == {"current": True}
        assert query_options() == {"old": True}


def test_sync_and_job_api_greetings_bypass_plan_review_and_stale_business_parent(tmp_path):
    service = make_service(intent=FakeIntent("complex"))
    config = settings(app_env="test", query_history_path=tmp_path / "history.sqlite", metric_registry_path=tmp_path / "metrics.sqlite")
    with TestClient(create_app(config, service_factory=lambda _: service)) as client:
        payload = {"question": "你好", "parent_query_id": "a" * 32, "review_plan": True}
        sync = client.post("/api/v1/queries", json=payload)
        assert sync.status_code == 200, sync.text
        job = client.post("/api/v1/query-jobs", json=payload)
        assert job.status_code == 202, job.text
        query_id = job.json()["id"]
        wait_until(lambda: client.get(f"/api/v1/history/{query_id}").json()["status"] == "succeeded")
        response = client.get(f"/api/v1/history/{query_id}").json()["response"]
        for data in (sync.json(), response):
            assert data["query_type"] == "non_query" and data["message"]
            assert data["sql"] == "" and data["plan"] is None
            assert data["analysis_context"]["conversation_only"] is True
            assert data["analysis_context"]["turns"] == []
        followup = client.post("/api/v1/query-jobs", json={"question": "订单数", "parent_query_id": query_id})
        assert followup.status_code == 409
        assert service.llm.calls == 0
        assert service.database.calls == 0
