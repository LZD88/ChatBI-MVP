"""Schema selection contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SchemaSelection:
    text: str
    mode: str
    tables: tuple[str, ...]
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SimpleSemanticSelection:
    schema: SchemaSelection
    indicator_text: str
    indicators: tuple[str, ...]
    fields: tuple[str, ...]
    joins: tuple[str, ...]
    mode: str


class SchemaProvider(Protocol):
    def select(self, question: str) -> SchemaSelection: ...
