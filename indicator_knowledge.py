"""Governed indicator definitions and dependency-aware prompt knowledge."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


REQUIRED_INDICATOR_FIELDS = (
    "name",
    "definition",
    "formula",
    "data_source",
    "depends_on",
    "time_field",
)


@dataclass(frozen=True, slots=True)
class IndicatorDefinition:
    """The governed six-field metric contract, plus retrieval-only metadata."""

    name: str
    definition: str
    formula: str
    data_source: str
    depends_on: tuple[str, ...]
    time_field: str
    aliases: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "IndicatorDefinition":
        missing = [field for field in REQUIRED_INDICATOR_FIELDS if field not in payload]
        if missing:
            raise ValueError(f"indicator is missing required fields: {', '.join(missing)}")

        text_fields = ("name", "definition", "formula", "data_source", "time_field")
        normalized: dict[str, str] = {}
        for field in text_fields:
            value = payload[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"indicator field {field} must be a non-empty string")
            normalized[field] = value.strip()

        return cls(
            name=normalized["name"],
            definition=normalized["definition"],
            formula=normalized["formula"],
            data_source=normalized["data_source"],
            depends_on=cls._string_tuple(payload["depends_on"], "depends_on"),
            time_field=normalized["time_field"],
            aliases=cls._string_tuple(payload.get("aliases", []), "aliases"),
            filters=cls._string_tuple(payload.get("filters", []), "filters"),
        )

    @staticmethod
    def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f"indicator field {field} must be a list of non-empty strings")
        return tuple(dict.fromkeys(item.strip() for item in value))

    def as_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "definition": self.definition,
            "formula": self.formula,
            "data_source": self.data_source,
            "depends_on": list(self.depends_on),
            "time_field": self.time_field,
            "aliases": list(self.aliases),
            "filters": list(self.filters),
        }


class IndicatorKnowledge:
    """Load, validate, match and recursively expand governed indicators."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        resolved_path = Path(config_path) if config_path else Path(__file__).with_name("indicators.json")
        document = json.loads(resolved_path.read_text(encoding="utf-8"))
        self._load_document(document)

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "IndicatorKnowledge":
        instance = cls.__new__(cls)
        instance._load_document(document)
        return instance

    def _load_document(self, document: Any) -> None:
        items = document.get("indicators") if isinstance(document, dict) else None
        if not isinstance(items, list) or not items:
            raise ValueError("indicator document must contain a non-empty indicators list")

        definitions = [IndicatorDefinition.from_mapping(item) for item in items]
        if len({item.name for item in definitions}) != len(definitions):
            raise ValueError("indicator names must be unique")
        self.definitions = {item.name: item for item in definitions}
        self._validate_dependencies()

        # Retain the historical mapping interface for retrievers and callers.
        self.indicators = {name: item.as_mapping() for name, item in self.definitions.items()}
        self.alias_map: dict[str, str] = {}
        for item in definitions:
            for alias in (item.name, *item.aliases):
                key = alias.lower()
                owner = self.alias_map.get(key)
                if owner is not None and owner != item.name:
                    raise ValueError(f"indicator alias {alias!r} belongs to multiple indicators")
                self.alias_map[key] = item.name

    def _validate_dependencies(self) -> None:
        for item in self.definitions.values():
            missing = [name for name in item.depends_on if name not in self.definitions]
            if missing:
                raise ValueError(f"indicator {item.name} has unknown dependencies: {', '.join(missing)}")

        visited: set[str] = set()
        visiting: list[str] = []

        def visit(name: str) -> None:
            if name in visiting:
                cycle = " -> ".join([*visiting[visiting.index(name):], name])
                raise ValueError(f"indicator dependency cycle: {cycle}")
            if name in visited:
                return
            visiting.append(name)
            for dependency in self.definitions[name].depends_on:
                visit(dependency)
            visiting.pop()
            visited.add(name)

        for name in self.definitions:
            visit(name)

    def detect_indicators(self, question: str) -> list[str]:
        """Match governed names/aliases, preferring longer and more specific aliases."""
        question_lower = question.lower()
        detected: list[str] = []
        occupied: list[tuple[int, int]] = []
        for alias, standard_name in sorted(self.alias_map.items(), key=lambda item: len(item[0]), reverse=True):
            start = 0
            while True:
                index = question_lower.find(alias, start)
                if index < 0:
                    break
                span = (index, index + len(alias))
                if not any(span[0] < other[1] and other[0] < span[1] for other in occupied):
                    if standard_name not in detected:
                        detected.append(standard_name)
                    occupied.append(span)
                start = index + max(len(alias), 1)
        return detected

    def resolve_dependencies(self, names: Iterable[str]) -> list[str]:
        """Return requested indicators followed by their transitive dependencies."""
        resolved: list[str] = []

        def append(name: str) -> None:
            if name not in self.definitions:
                raise KeyError(f"unknown indicator: {name}")
            if name in resolved:
                return
            resolved.append(name)
            for dependency in self.definitions[name].depends_on:
                append(dependency)

        for name in names:
            append(name)
        return resolved

    def get_indicator_text(self, indicator_name: str) -> str:
        item = self.definitions.get(indicator_name)
        if item is None:
            return ""
        lines = [
            f"指标：{item.name}",
            f"  业务定义：{item.definition}",
            f"  SQL公式：{item.formula}",
            f"  数据来源：{item.data_source}",
            f"  时间字段：{item.time_field}",
        ]
        if item.depends_on:
            lines.append(f"  依赖指标：{', '.join(item.depends_on)}")
        if item.filters:
            lines.append(f"  强制过滤：{' AND '.join(item.filters)}")
        return "\n".join(lines)

    def build_knowledge_for_names(self, names: Iterable[str], *, title: str = "【指标知识】") -> str:
        expanded = self.resolve_dependencies(names)
        if not expanded:
            return ""
        return "\n\n".join([title, *(self.get_indicator_text(name) for name in expanded)])

    def build_knowledge_block(self, question: str) -> str:
        return self.build_knowledge_for_names(self.detect_indicators(question))


if __name__ == "__main__":
    knowledge = IndicatorKnowledge()
    for sample in ("查询上个月的利润", "按产品线统计毛利", "查询已完成订单的总数量"):
        print(f"\n问题：{sample}")
        print(knowledge.build_knowledge_block(sample) or "未识别到指标")
