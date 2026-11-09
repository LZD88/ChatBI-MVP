"""Semantic/static schema providers and index lifecycle commands."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from chatbi.domain.schema import SchemaProvider, SchemaSelection
from chatbi.domain.security import DEFAULT_SCHEMA
from chatbi.settings import Settings, get_settings
from prompt_builder import SCHEMA


JOIN_HINTS = {
    frozenset({"sales_orders", "dim_customers"}): "关联：sales_orders.customer_id = dim_customers.customer_id",
    frozenset({"sales_orders", "dim_products"}): "关联：sales_orders.product_id = dim_products.product_id",
    frozenset({"sales_orders", "exchange_rates"}): (
        "关联：sales_orders.order_date = exchange_rates.rate_date AND "
        "sales_orders.currency = exchange_rates.currency"
    ),
}


class SemanticSchemaProvider:
    def __init__(self, retriever: Callable[[str], dict] | None = None) -> None:
        if retriever is None:
            from field_matcher import retrieve_schema
            retriever = retrieve_schema
        self.retriever = retriever

    def select(self, question: str) -> SchemaSelection:
        result = self.retriever(question)
        table_names = tuple(item["table_name"] for item in result.get("tables", []))
        if not table_names or not result.get("fields"):
            raise LookupError("semantic schema retrieval returned no candidates")
        lines = [result.get("schema_snippet", "").strip()]
        table_set = set(table_names)
        for tables, hint in JOIN_HINTS.items():
            if tables.issubset(table_set):
                lines.append(hint)
        return SchemaSelection("\n".join(line for line in lines if line), "semantic", table_names)


class StaticSchemaProvider:
    def select(self, question: str) -> SchemaSelection:
        return SchemaSelection(
            SCHEMA.strip(),
            "fallback",
            tuple(DEFAULT_SCHEMA),
        )


class FallbackSchemaProvider:
    def __init__(self, primary: SchemaProvider, fallback: SchemaProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    def select(self, question: str) -> SchemaSelection:
        try:
            return self.primary.select(question)
        except Exception as exc:
            fallback = self.fallback.select(question)
            return SchemaSelection(fallback.text, "fallback", fallback.tables, type(exc).__name__)


def index_manifest_path(settings: Settings | None = None) -> Path:
    resolved = settings or get_settings()
    return resolved.chroma_persist_dir / "manifest.json"


def indexes_ready(settings: Settings | None = None) -> bool:
    resolved = settings or get_settings()
    marker = index_manifest_path(resolved)
    if not marker.is_file():
        return False
    try:
        manifest = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        manifest.get("version") == resolved.schema_index_version
        and (resolved.chroma_persist_dir / "tables").exists()
        and (resolved.chroma_persist_dir / "fields").exists()
    )


def build_schema_indexes(
    *,
    force: bool = False,
    settings: Settings | None = None,
    table_builder: Callable[[bool], object] | None = None,
    field_builder: Callable[[bool], object] | None = None,
) -> Path:
    resolved = settings or get_settings()
    if table_builder is None:
        from table_retriever import build_index
        table_builder = build_index
    if field_builder is None:
        from field_matcher import build_field_index
        field_builder = build_field_index
    resolved.chroma_persist_dir.mkdir(parents=True, exist_ok=True)
    table_builder(force)
    field_builder(force)
    marker = index_manifest_path(resolved)
    marker.write_text(
        json.dumps({"version": resolved.schema_index_version}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return marker


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ChatBI table and field indexes")
    parser.add_argument("--force", action="store_true", help="rebuild existing indexes")
    args = parser.parse_args()
    marker = build_schema_indexes(force=args.force)
    print(f"Schema indexes ready: {marker}")
