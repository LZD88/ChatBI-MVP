from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from chatbi.api.app import create_app, refresh_query_catalog
from chatbi.infrastructure.metric_registry import CatalogDecision, CatalogError, MetricDraft, MetricRegistry
from chatbi.settings import Settings
from tests.test_api import FakeDatabase, FakeService
from tests.test_query_service import make_service


def payload(**changes):
    return MetricDraft(**{
        "name": "测试销售规模", "definition": "已完成订单收入合计", "formula": "SUM(sales_orders.net_amount)",
        "data_source": "sales_orders", "time_field": "sales_orders.order_date", "owner": "销售负责人",
        "change_note": "建立测试口径", **changes,
    })


def decision():
    return CatalogDecision(actor="数据管理员", note="已复核口径与依赖")


def publish(registry, draft):
    registry.transition(draft["id"], draft["version"], "submit", decision())
    return registry.transition(draft["id"], draft["version"], "publish", decision())


def test_import_once_versions_publish_and_persistence(tmp_path):
    path = tmp_path / "metrics.sqlite"
    registry = MetricRegistry(path)
    revision, knowledge = registry.snapshot()
    assert len(knowledge.indicators) == 61
    draft = registry.create_draft(payload())
    assert registry.snapshot()[0] == revision
    publish(registry, draft)
    assert len(registry.snapshot()[1].indicators) == 62
    assert MetricRegistry(path).snapshot()[0] == registry.snapshot()[0]
    second = registry.create_draft(payload(formula="SUM(sales_orders.quantity)"), draft["id"])
    assert registry.snapshot()[1].definitions["测试销售规模"].formula == "SUM(sales_orders.net_amount)"
    publish(registry, second)
    detail = registry.detail(draft["id"])
    assert [item["version"] for item in detail["versions"]] == [2, 1]
    assert detail["versions"][0]["approved_by"] == "数据管理员"
    assert len(detail["events"]) == 6
    registry.transition(draft["id"], 2, "retire", decision())
    assert "测试销售规模" not in MetricRegistry(path).snapshot()[1].indicators


def test_publish_checks_alias_unknown_dependency_and_cycle_atomically(tmp_path):
    registry = MetricRegistry(tmp_path / "metrics.sqlite")
    draft = registry.create_draft(payload(aliases=["销售额"]))
    registry.transition(draft["id"], 1, "submit", decision())
    revision = registry.snapshot()[0]
    with pytest.raises(CatalogError, match="alias"):
        registry.transition(draft["id"], 1, "publish", decision())
    assert registry.snapshot()[0] == revision
    registry.transition(draft["id"], 1, "reject", decision())
    second = registry.create_draft(payload(depends_on=["不存在的指标"]), draft["id"])
    registry.transition(draft["id"], 2, "submit", decision())
    with pytest.raises(CatalogError, match="unknown dependencies"):
        registry.transition(draft["id"], 2, "publish", decision())
    registry.transition(draft["id"], 2, "reject", decision())
    third = registry.create_draft(payload(depends_on=["测试销售规模"]), draft["id"])
    registry.transition(draft["id"], 3, "submit", decision())
    with pytest.raises(CatalogError, match="cycle"):
        registry.transition(draft["id"], third["version"], "publish", decision())


def test_retirement_blocks_dependents_and_stale_version(tmp_path):
    registry = MetricRegistry(tmp_path / "metrics.sqlite")
    parent = registry.create_draft(payload())
    publish(registry, parent)
    child = registry.create_draft(payload(name="测试派生指标", depends_on=["测试销售规模"]))
    publish(registry, child)
    assert registry.detail(parent["id"])["downstream"] == ["测试派生指标"]
    with pytest.raises(CatalogError, match="unknown dependencies"):
        registry.transition(parent["id"], 1, "retire", decision())
    revision = registry.create_draft(payload(), parent["id"])
    publish(registry, revision)
    with pytest.raises(CatalogError, match="当前生效"):
        registry.transition(parent["id"], 1, "retire", decision())


def test_concurrent_duplicate_creation_is_rejected_and_pending_version_immutable(tmp_path):
    path = tmp_path / "metrics.sqlite"
    registry = MetricRegistry(path)
    def create():
        try:
            return MetricRegistry(path).create_draft(payload())
        except CatalogError:
            return None
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda _: create(), range(2)))
    draft = next(result for result in results if result)
    assert sum(result is not None for result in results) == 1
    with pytest.raises(CatalogError, match="待处理"):
        registry.create_draft(payload(), draft["id"])
    with pytest.raises(CatalogError, match="名称"):
        registry.create_draft(payload(name="改名"), draft["id"])


def test_metric_api_works_without_model_and_blocks_cross_site_write(tmp_path):
    config = Settings(_env_file=None, app_env="test", metric_registry_path=tmp_path / "metrics.sqlite")
    with TestClient(create_app(config, service_factory=lambda _: None)) as client:
        assert client.get("/metrics").status_code == 200
        assert client.get("/api/v1/metrics").json()["active_count"] == 61
        blocked = client.post("/api/v1/metrics", json=payload().model_dump(), headers={"Origin": "https://evil.example"})
        assert blocked.status_code == 403
        created = client.post("/api/v1/metrics", json=payload().model_dump())
        assert created.status_code == 201
        item = created.json()
        for action in ("submit", "publish"):
            assert client.post(f"/api/v1/metrics/{item['id']}/versions/1/{action}", json=decision().model_dump()).status_code == 200
        assert client.get("/api/v1/metrics").json()["active_count"] == 62
        assert client.post("/api/v1/metrics", json=payload().model_dump()).status_code == 409
    # The local guard cannot be bypassed with a hostile Host header in production mode.
    config.app_env = "dev"
    with TestClient(create_app(config, service_factory=lambda _: None)) as client:
        assert client.get("/api/v1/metrics", headers={"Host": "evil.example"}).status_code == 403


def test_published_snapshot_reaches_actual_query_and_planner_consumers(tmp_path):
    registry = MetricRegistry(tmp_path / "metrics.sqlite")
    draft = registry.create_draft(payload(aliases=["自定义销售规模"]))
    publish(registry, draft)
    service = make_service()
    refresh_query_catalog(service, registry.snapshot()[1])
    assert service.indicators.detect_indicators("自定义销售规模") == ["测试销售规模"]
    assert service.complex_semantic.indicators is service.indicators


def test_query_api_exposes_chart_and_catalog_revision(tmp_path):
    config = Settings(_env_file=None, metric_registry_path=tmp_path / "metrics.sqlite")
    with TestClient(create_app(config, service_factory=lambda _: FakeService(FakeDatabase()))) as client:
        response = client.post("/api/v1/queries", json={"question": "订单数"})
        assert response.status_code == 200
        assert response.json()["catalog_revision"]
        assert response.json()["visualization_spec"][0]["type"] == "kpi"
