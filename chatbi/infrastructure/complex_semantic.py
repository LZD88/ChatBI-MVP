"""Bounded complex-query retrieval using Chroma and a governed local fallback."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from chatbi.application.ports import SimpleSemanticPort
from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.domain.schema import SchemaSelection, SimpleSemanticSelection
from chatbi.domain.security import DEFAULT_SCHEMA
from chatbi.infrastructure.simple_semantic import (
    ChromaSimpleSemanticPipeline, LocalFeatureEncoder, TABLE_DESCRIPTIONS, TABLE_KEYWORDS,
)
from chatbi.settings import Settings
from indicator_knowledge import IndicatorKnowledge

logger = logging.getLogger("chatbi.complex_semantic")


class ComplexSemanticResolver:
    def __init__(
        self, settings: Settings, indicators: IndicatorKnowledge,
        chroma: SimpleSemanticPort | None = None,
    ) -> None:
        self.settings = settings
        self.indicators = indicators
        self.chroma = chroma

    def resolve(
        self, question: str, *, required_tables: Iterable[str] = (),
        indicator_names: Iterable[str] = (),
    ) -> SimpleSemanticSelection:
        names = list(dict.fromkeys([
            *indicator_names, *self.indicators.detect_indicators(question),
        ]))
        recalled: list[str] = []
        mode = "governed_local"
        if self.chroma is not None:
            try:
                result = self.chroma.resolve(question)
                recalled = list(result.schema.tables)
                if not names:
                    names = list(result.indicators)
                mode = "chroma"
            except Exception as exc:
                logger.warning("complex_chroma_fallback reason=%s", type(exc).__name__)

        allowed = self.settings.allowed_tables & frozenset(DEFAULT_SCHEMA)
        mandatory = list(dict.fromkeys(required_tables))
        if set(mandatory) - allowed:
            raise ChatBIError(ErrorCode.SCHEMA_UNAVAILABLE, "分析步骤引用了不可用的数据表。")
        expanded_names = self.indicators.resolve_dependencies(names)
        knowledge = self.indicators.build_knowledge_for_names(names)
        for name in expanded_names:
            item = self.indicators.definitions[name]
            text = f"{item.data_source} {item.formula} {item.time_field}"
            mandatory.extend(table for table in sorted(allowed) if re.search(rf"\b{table}\b", text))
        lexical = [
            table for table, keywords in TABLE_KEYWORDS.items()
            if table in allowed and any(word in question for word in keywords)
        ]
        selected = list(dict.fromkeys([*mandatory, *lexical, *[t for t in recalled if t in allowed]]))
        if not selected:
            encoder = LocalFeatureEncoder()
            query = encoder.encode(question)
            selected = sorted(
                allowed,
                key=lambda table: sum(a * b for a, b in zip(query, encoder.encode(TABLE_DESCRIPTIONS[table]))),
                reverse=True,
            )[:3]
        selected, _joins = ChromaSimpleSemanticPipeline._infer_joins(selected)
        selected = [table for table in selected if table in allowed]
        if not selected or len(selected) > self.settings.complex_schema_max_tables:
            raise ChatBIError(
                ErrorCode.SCHEMA_UNAVAILABLE,
                "分析涉及的数据表超过精简上下文上限，请缩小分析范围。",
            )
        # All fields of the selected tables are available to the planner. This
        # avoids hiding join/filter fields without sending the entire database.
        fields = [f"{table}.{field}" for table in selected for field in sorted(DEFAULT_SCHEMA[table])]
        joins = [
            join for join in _joins
            if all(table in allowed for table in re.findall(r"(\w+)\.", join))
        ]
        text = ChromaSimpleSemanticPipeline._build_schema(selected, fields, joins)
        if mode != "chroma":
            text = text.replace("Chroma 动态精简 Schema", "本地治理精简 Schema")
        return SimpleSemanticSelection(
            schema=SchemaSelection(text, "semantic" if mode == "chroma" else mode, tuple(selected), mode),
            indicator_text=knowledge, indicators=tuple(names), fields=tuple(fields),
            joins=tuple(joins), mode=mode,
        )
