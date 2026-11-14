"""Versioned local control-plane catalog, independent of read-only business SQL."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from indicator_knowledge import IndicatorDefinition, IndicatorKnowledge


class CatalogError(ValueError):
    pass


class MetricDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    definition: str = Field(min_length=1, max_length=2000)
    formula: str = Field(min_length=1, max_length=4000)
    data_source: str = Field(min_length=1, max_length=2000)
    time_field: str = Field(min_length=1, max_length=300)
    depends_on: list[str] = Field(default_factory=list, max_length=30)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    filters: list[str] = Field(default_factory=list, max_length=30)
    owner: str = Field(min_length=1, max_length=80)
    change_note: str = Field(min_length=1, max_length=1000)

    @field_validator("depends_on", "aliases", "filters")
    @classmethod
    def clean_list(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 500 for value in values):
            raise ValueError("列表项不能为空或超过 500 字符")
        return list(dict.fromkeys(value.strip() for value in values))

    def definition_mapping(self) -> dict[str, Any]:
        return IndicatorDefinition.from_mapping(self.model_dump()).as_mapping()


class CatalogDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    actor: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=1, max_length=1000)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MetricRegistry:
    """SQLite transactions serialize immutable revisions and publication validation.

    Actors are local audit labels, not authenticated identities. No business DB
    account needs write permission. Only published, non-retired versions are used.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = RLock()
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE
                );
                CREATE TABLE IF NOT EXISTS metric_versions (
                    metric_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
                    owner TEXT NOT NULL, change_note TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, effective_at TEXT,
                    approved_by TEXT, PRIMARY KEY (metric_id, version),
                    FOREIGN KEY(metric_id) REFERENCES metrics(id)
                );
                CREATE TABLE IF NOT EXISTS metric_events (
                    id INTEGER PRIMARY KEY, metric_id TEXT NOT NULL, version INTEGER NOT NULL,
                    action TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL, at TEXT NOT NULL
                );
            """)
            # Seed exactly once, not whenever a metric is retired or drafts exist.
            if not connection.execute("SELECT 1 FROM metrics LIMIT 1").fetchone():
                for definition in IndicatorKnowledge().indicators.values():
                    metric_id = uuid4().hex
                    connection.execute("INSERT INTO metrics VALUES (?, ?)", (metric_id, definition["name"]))
                    connection.execute(
                        "INSERT INTO metric_versions VALUES (?,1,?,?,?,'published',?,?,?)",
                        (metric_id, json.dumps(definition, ensure_ascii=False), "待认领", "导入初始治理目录", now(), now(), "系统导入"),
                    )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            connection = sqlite3.connect(self.path, timeout=10)
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("BEGIN IMMEDIATE")
                with connection:
                    yield connection
            finally:
                connection.close()

    @staticmethod
    def _active(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        rows = connection.execute("""
            SELECT m.id, v.payload, v.status FROM metrics m JOIN metric_versions v ON v.metric_id=m.id
            WHERE v.effective_at IS NOT NULL AND v.version=(
              SELECT MAX(version) FROM metric_versions WHERE metric_id=m.id AND effective_at IS NOT NULL
            )
        """).fetchall()
        return {row["id"]: json.loads(row["payload"]) for row in rows if row["status"] == "published"}

    def snapshot(self) -> tuple[str, IndicatorKnowledge]:
        with self._connection() as connection:
            values = sorted(self._active(connection).values(), key=lambda item: item["name"])
            document = {"indicators": values}
            digest = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            return digest, IndicatorKnowledge.from_document(document)

    def list_metrics(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            active = self._active(connection)
            rows = connection.execute("""
                SELECT m.id, m.name, v.* FROM metrics m JOIN metric_versions v ON m.id=v.metric_id
                WHERE v.version=(SELECT MAX(version) FROM metric_versions WHERE metric_id=m.id)
                ORDER BY m.name
            """).fetchall()
            return [{**dict(row), "payload": json.loads(row["payload"]), "active": row["id"] in active} for row in rows]

    def detail(self, metric_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            metric = connection.execute("SELECT * FROM metrics WHERE id=?", (metric_id,)).fetchone()
            if not metric:
                raise CatalogError("指标不存在")
            versions = connection.execute("SELECT * FROM metric_versions WHERE metric_id=? ORDER BY version DESC", (metric_id,)).fetchall()
            active = self._active(connection)
            name = metric["name"]
            return {**dict(metric), "versions": [{**dict(row), "payload": json.loads(row["payload"])} for row in versions],
                    "downstream": [value["name"] for value in active.values() if name in value["depends_on"]],
                    "events": [dict(row) for row in connection.execute("SELECT * FROM metric_events WHERE metric_id=? ORDER BY id DESC", (metric_id,))]}

    def create_draft(self, payload: MetricDraft, metric_id: str | None = None) -> dict[str, Any]:
        mapping = payload.definition_mapping()
        with self._connection() as connection:
            if metric_id:
                metric = connection.execute("SELECT name FROM metrics WHERE id=?", (metric_id,)).fetchone()
                if not metric or metric["name"] != payload.name:
                    raise CatalogError("指标不存在或名称被修改；已有指标请保持名称不变")
            else:
                if connection.execute("SELECT 1 FROM metrics WHERE name=? COLLATE NOCASE", (payload.name,)).fetchone():
                    raise CatalogError("指标名称已存在，请为原指标创建新版本")
                metric_id = uuid4().hex
                connection.execute("INSERT INTO metrics VALUES (?,?)", (metric_id, payload.name))
            pending = connection.execute("SELECT 1 FROM metric_versions WHERE metric_id=? AND status IN ('draft','in_review')", (metric_id,)).fetchone()
            if pending:
                raise CatalogError("已有待处理版本，请先发布或驳回")
            version = connection.execute("SELECT COALESCE(MAX(version),0)+1 FROM metric_versions WHERE metric_id=?", (metric_id,)).fetchone()[0]
            connection.execute("INSERT INTO metric_versions VALUES (?,?,?,?,?,'draft',?,NULL,NULL)",
                               (metric_id, version, json.dumps(mapping, ensure_ascii=False), payload.owner, payload.change_note, now()))
            self._event(connection, metric_id, version, "draft", payload.owner, payload.change_note)
        return {"id": metric_id, "version": version, "status": "draft"}

    def transition(self, metric_id: str, version: int, action: str, decision: CatalogDecision) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM metric_versions WHERE metric_id=? AND version=?", (metric_id, version)).fetchone()
            if not row:
                raise CatalogError("指标版本不存在")
            allowed = {"submit": ("draft", "in_review"), "publish": ("in_review", "published"),
                       "reject": ("in_review", "rejected"), "discard": ("draft", "rejected"),
                       "retire": ("published", "retired")}
            if action not in allowed or row["status"] != allowed[action][0]:
                raise CatalogError("版本状态已变化或不支持该操作，请刷新")
            active = self._active(connection)
            if action in {"publish", "retire"}:
                if action == "publish":
                    active[metric_id] = json.loads(row["payload"])
                else:
                    latest = connection.execute("SELECT MAX(version) FROM metric_versions WHERE metric_id=? AND effective_at IS NOT NULL", (metric_id,)).fetchone()[0]
                    if latest != version:
                        raise CatalogError("只能废弃当前生效版本")
                    active.pop(metric_id, None)
                try:
                    IndicatorKnowledge.from_document({"indicators": list(active.values())})
                except (ValueError, KeyError) as exc:
                    raise CatalogError(f"目录校验失败：{exc}") from exc
            status = allowed[action][1]
            connection.execute("UPDATE metric_versions SET status=?, effective_at=?, approved_by=? WHERE metric_id=? AND version=?",
                               (status, now() if action == "publish" else row["effective_at"],
                                decision.actor if action == "publish" else row["approved_by"], metric_id, version))
            self._event(connection, metric_id, version, action, decision.actor, decision.note)
        return {"id": metric_id, "version": version, "status": status}

    @staticmethod
    def _event(connection: sqlite3.Connection, metric_id: str, version: int, action: str, actor: str, note: str) -> None:
        connection.execute("INSERT INTO metric_events(metric_id,version,action,actor,note,at) VALUES (?,?,?,?,?,?)",
                           (metric_id, version, action, actor, note, now()))
