from pathlib import Path

import pytest

from chatbi.infrastructure.schema import (
    FallbackSchemaProvider,
    SemanticSchemaProvider,
    StaticSchemaProvider,
    build_schema_indexes,
    indexes_ready,
)
from chatbi.settings import Settings


def semantic_result() -> dict:
    return {
        "tables": [{"table_name": "sales_orders"}, {"table_name": "dim_products"}],
        "fields": [
            {"table": "sales_orders", "field": "product_id"},
            {"table": "dim_products", "field": "product_line"},
        ],
        "schema_snippet": "表：sales_orders（相关字段：product_id）\n表：dim_products（相关字段：product_line）",
    }


def test_semantic_provider_adds_required_join_key_hint() -> None:
    selection = SemanticSchemaProvider(lambda _: semantic_result()).select("按产品线统计收入")
    assert selection.mode == "semantic"
    assert "sales_orders.product_id = dim_products.product_id" in selection.text


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError("index"), RuntimeError("embedding"), LookupError("empty")],
)
def test_fallback_provider_records_reason(failure: Exception) -> None:
    def fail(_: str):
        raise failure

    selection = FallbackSchemaProvider(
        SemanticSchemaProvider(fail), StaticSchemaProvider()
    ).select("收入")
    assert selection.mode == "fallback"
    assert selection.reason == type(failure).__name__
    assert "sales_orders" in selection.text


def test_empty_semantic_candidates_trigger_fallback() -> None:
    provider = FallbackSchemaProvider(
        SemanticSchemaProvider(lambda _: {"tables": [], "fields": []}), StaticSchemaProvider()
    )
    assert provider.select("收入").mode == "fallback"


def test_index_lifecycle_build_load_and_rebuild(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, chroma_persist_dir=tmp_path, schema_index_version="v-test")
    calls: list[tuple[str, bool]] = []

    def table_builder(force: bool) -> None:
        calls.append(("tables", force))
        (tmp_path / "tables").mkdir(exist_ok=True)

    def field_builder(force: bool) -> None:
        calls.append(("fields", force))
        (tmp_path / "fields").mkdir(exist_ok=True)

    marker = build_schema_indexes(
        settings=settings, table_builder=table_builder, field_builder=field_builder
    )
    assert marker.is_file()
    assert indexes_ready(settings) is True

    build_schema_indexes(
        force=True, settings=settings, table_builder=table_builder, field_builder=field_builder
    )
    assert calls[-2:] == [("tables", True), ("fields", True)]
