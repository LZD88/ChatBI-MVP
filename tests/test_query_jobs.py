from pathlib import Path
from queue import Full
from threading import Event
from time import monotonic, sleep

from fastapi.testclient import TestClient
import pytest

from chatbi.api.app import create_app
from chatbi.application.progress import checkpoint, progress
from chatbi.application.query_jobs import QueryJobs
from chatbi.infrastructure.query_history import QueryHistory, TERMINAL
from chatbi.settings import Settings
from tests.test_api import FakeService, FakeDatabase


def wait_until(read, timeout=3):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        value = read()
        if value:
            return value
        sleep(.01)
    raise AssertionError("state transition timed out")


def test_cooperative_cancel_does_not_execute_following_stage(tmp_path):
    history = QueryHistory(tmp_path / "history.sqlite")
    entered, release = Event(), Event()
    later = []
    def execute(question, query_id):
        progress("planning", message="规划中")
        entered.set()
        release.wait(2)
        checkpoint()
        later.append("database")
        return {"rows": [[1]]}
    jobs = QueryJobs(history, execute)
    try:
        query_id = jobs.submit("分析")
        assert entered.wait(2)
        assert jobs.cancel(query_id)
        assert history.get(query_id)["status"] == "cancelling"
        release.set()
        wait_until(lambda: history.get(query_id)["status"] == "cancelled")
        assert later == []
        assert not history.get(query_id)["response"]
        assert any(event["stage"] == "planning" for event in history.events(query_id))
    finally:
        release.set(); jobs.close()


def test_queue_is_bounded_timeout_and_cancellation_never_run_queued_work(tmp_path):
    history = QueryHistory(tmp_path / "history.sqlite")
    entered, release = Event(), Event()
    calls = []
    def execute(question, query_id):
        calls.append(question); entered.set(); release.wait(2); checkpoint(); return {"question": question}
    jobs = QueryJobs(history, execute, capacity=1, timeout=.08)
    try:
        first = jobs.submit("第一问"); assert entered.wait(1)
        second = jobs.submit("第二问")
        with pytest.raises(Full):
            jobs.submit("第三问")
        sleep(.1); release.set()
        wait_until(lambda: history.get(second)["status"] == "timed_out")
        assert history.get(first)["status"] == "timed_out"
        assert calls == ["第一问"]
    finally:
        release.set(); jobs.close()


def test_queued_cancel_is_immediate_and_can_be_deleted_before_worker_reaches_it(tmp_path):
    history = QueryHistory(tmp_path / "history.sqlite")
    entered, release = Event(), Event()
    calls = []
    def execute(question, query_id):
        calls.append(question); entered.set(); release.wait(2); return {"question": question}
    jobs = QueryJobs(history, execute)
    try:
        first = jobs.submit("第一问"); assert entered.wait(1)
        second = jobs.submit("第二问")
        assert jobs.cancel(second)
        assert history.get(second)["status"] == "cancelled"
        history.clear(second)
        release.set()
        wait_until(lambda: history.get(first)["status"] == "succeeded")
        wait_until(lambda: jobs.queue.unfinished_tasks == 0)
        assert calls == ["第一问"]
        assert history.get(second) is None
    finally:
        release.set(); jobs.close()


def test_history_recovery_annotations_retention_and_clear(tmp_path):
    path = tmp_path / "history.sqlite"
    history = QueryHistory(path, retain=2)
    history.create("star", "保留"); history.annotate("star", {"favorite": True, "feedback": "useful"})
    history.finish("star", "succeeded", {"rows": [[1]], "catalog_revision": "v1"})
    for index in range(3):
        history.create(str(index), f"问题{index}"); history.finish(str(index), "failed", error={"code": "test"})
    assert len(history.list_history()) == 3
    history.create("pending", "中断问题")
    restored = QueryHistory(path, retain=2); restored.recover()
    assert restored.get("pending")["status"] == "interrupted"
    assert restored.get("star")["response"]["catalog_revision"] == "v1"
    assert len(restored.list_history(favorites=True)) == 1
    restored.clear()
    assert [item["id"] for item in restored.list_history()] == ["star"]
    assert restored.events("star")
    restored.clear("star")
    assert not restored.events("star")


def test_job_api_sse_replay_and_persistent_response_after_restart(tmp_path):
    settings = Settings(_env_file=None, app_env="test", query_history_path=tmp_path / "history.sqlite",
                        metric_registry_path=tmp_path / "metrics.sqlite")
    with TestClient(create_app(settings, service_factory=lambda _: FakeService(FakeDatabase()))) as client:
        created = client.post("/api/v1/query-jobs", json={"question": "订单数"})
        assert created.status_code == 202
        query_id = created.json()["id"]
        wait_until(lambda: client.get(f"/api/v1/history/{query_id}").json()["status"] in TERMINAL)
        item = client.get(f"/api/v1/history/{query_id}").json()
        assert item["response"]["visualization_spec"][0]["type"] == "kpi"
        response = client.get(f"/api/v1/query-jobs/{query_id}/events", headers={"Last-Event-ID": "1"})
        assert response.status_code == 200
        assert "event: done" in response.text and "event: progress" in response.text
        assert "id: 1\n" not in response.text
        assert client.patch(f"/api/v1/history/{query_id}", json={"favorite": True, "feedback": "useful"}).status_code == 200
        assert client.delete("/api/v1/history").json()["deleted"] == 0
        assert client.get("/api/v1/runtime").json()["restart_required"] is False
        assert client.post("/api/v1/query-jobs", json={"question": " "}).status_code == 422
    with TestClient(create_app(settings, service_factory=lambda _: FakeService(FakeDatabase()))) as client:
        assert client.get(f"/api/v1/history/{query_id}").json()["response"]["rows"] == [[7]]
        assert client.get("/api/v1/history?favorites=true").json()["items"][0]["id"] == query_id


def test_active_job_does_not_block_health_and_local_guard_protects_history(tmp_path):
    entered, release = Event(), Event()
    class SlowService(FakeService):
        def query(self, *args, **kwargs):
            progress("planning", message="正在规划"); entered.set(); release.wait(2); checkpoint()
            return super().query(*args, **kwargs)
    settings = Settings(_env_file=None, app_env="test", query_history_path=tmp_path / "history.sqlite")
    with TestClient(create_app(settings, service_factory=lambda _: SlowService(FakeDatabase()))) as client:
        try:
            query_id = client.post("/api/v1/query-jobs", json={"question": "分析"}).json()["id"]
            assert entered.wait(1)
            assert client.get("/health/live").status_code == 200
            assert client.post(f"/api/v1/query-jobs/{query_id}/cancel").json()["accepted"]
            assert client.delete(f"/api/v1/history/{query_id}").status_code == 409
            release.set()
            wait_until(lambda: client.get(f"/api/v1/history/{query_id}").json()["status"] == "cancelled")
            assert client.post("/api/v1/query-jobs", json={"question": "分析"}, headers={"Origin": "https://evil.example"}).status_code == 403
        finally:
            release.set()
