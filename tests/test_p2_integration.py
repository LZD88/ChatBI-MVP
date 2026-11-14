"""Explicitly enabled Chroma check for a same-size published catalog revision."""

import os

import pytest

from chatbi.infrastructure.metric_registry import CatalogDecision, MetricDraft, MetricRegistry
from chatbi.infrastructure.simple_semantic import ChromaSimpleSemanticPipeline
from chatbi.settings import Settings
from indicator_knowledge import IndicatorKnowledge


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("CHATBI_CHROMA_TEST") != "1", reason="requires VM Chroma")
def test_published_formula_refreshes_chroma_even_when_metric_count_is_unchanged(tmp_path):
    registry = MetricRegistry(tmp_path / "metrics.sqlite")
    income = next(item for item in registry.list_metrics() if item["name"] == "收入")
    old = registry.snapshot()[1]
    semantic = ChromaSimpleSemanticPipeline(Settings(_env_file=None, simple_chroma_dir=tmp_path / "semantic"), old)
    draft = registry.create_draft(MetricDraft(**{**income["payload"], "definition": "口径刷新验证：已完成订单的不含税人民币收入",
                                               "aliases": [*income["payload"]["aliases"], "口径刷新测试营收"],
                                               "owner": "测试责任人", "change_note": "验证等数量指标版本的索引刷新"}), income["id"])
    decision = CatalogDecision(actor="测试审核人", note="仅修改隔离测试目录")
    registry.transition(draft["id"], draft["version"], "submit", decision)
    registry.transition(draft["id"], draft["version"], "publish", decision)
    try:
        semantic.update_knowledge(registry.snapshot()[1])
        assert semantic.resolve("口径刷新测试营收").indicators == ("收入",)
        stored = semantic.indicators.get(ids=["indicator:收入"], include=["documents"])
        assert "口径刷新验证" in stored["documents"][0]
        assert semantic.indicators.count() == 61
    finally:
        semantic.update_knowledge(IndicatorKnowledge())
        semantic.resolve("销售额")
