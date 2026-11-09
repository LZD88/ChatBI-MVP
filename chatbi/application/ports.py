"""Application ports keep orchestration independent from vendor libraries."""

from __future__ import annotations

from typing import Any, Protocol

from chatbi.domain.models import QueryIntent
from chatbi.domain.schema import SchemaSelection, SimpleSemanticSelection
from chatbi.domain.security import SecuredQuery, UserContext


class LLMPort(Protocol):
    def generate_sql(self, system_msg: str, prompt: str) -> str: ...

    def generate_text(self, system_msg: str, prompt: str) -> str: ...


class QueryIntentPort(Protocol):
    def classify(self, question: str) -> QueryIntent: ...


class SimpleSemanticPort(Protocol):
    def resolve(self, question: str) -> SimpleSemanticSelection: ...


class SchemaProviderPort(Protocol):
    def select(self, question: str) -> SchemaSelection: ...


class SQLSecurityPort(Protocol):
    def secure(self, sql: str, user: UserContext | None = None) -> SecuredQuery: ...

    def mask_result(
        self, columns: list[str], rows: list[tuple], user: UserContext | None = None
    ) -> tuple[list[str], list[tuple]]: ...


class DatabasePort(Protocol):
    def execute(self, query: SecuredQuery) -> tuple[list[str], list[tuple[Any, ...]]]: ...

    def validate_connection(self) -> bool: ...

    def close(self) -> None: ...
