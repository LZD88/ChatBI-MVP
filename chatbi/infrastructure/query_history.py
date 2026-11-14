"""Bounded local history with immutable query responses and replayable events."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from chatbi.application.failure_control import encode_state, decode_state

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "timed_out", "suspended"}


class ResumeConflict(ValueError):
    pass


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class QueryHistory:
    def __init__(self, path: Path, retain: int = 200) -> None:
        self.path, self.retain = path, retain
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS queries (
                  id TEXT PRIMARY KEY, question TEXT NOT NULL, status TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  response TEXT, error TEXT, favorite INTEGER NOT NULL DEFAULT 0,
                  feedback TEXT, feedback_note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS query_events (
                  query_id TEXT NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
                  sequence INTEGER NOT NULL, stage TEXT NOT NULL, detail TEXT NOT NULL,
                  at TEXT NOT NULL, PRIMARY KEY(query_id,sequence)
                );
                CREATE TABLE IF NOT EXISTS analysis_checkpoints (
                  query_id TEXT PRIMARY KEY REFERENCES queries(id) ON DELETE CASCADE,
                  state TEXT NOT NULL, updated_at TEXT NOT NULL
                );
            """)
            if "request_options" not in {row[1] for row in db.execute("PRAGMA table_info(queries)")}:
                db.execute("ALTER TABLE queries ADD COLUMN request_options TEXT")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def recover(self) -> None:
        with self.connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM queries WHERE status IN ('queued','running','cancelling')")]
        for query_id in ids:
            if self.checkpoint(query_id) is not None:
                self.status(query_id, "suspended")
                self.event(query_id, "suspended", {"message": "续跑被服务重启中断，原挂起断点保留，可再次确认恢复。"})
            else:
                self.finish(query_id, "interrupted", error={"code": "interrupted", "message": "服务重启中断了该查询，请重新运行。"})

    def checkpoint(self, query_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT state FROM analysis_checkpoints WHERE query_id=?", (query_id,)).fetchone()
        return decode_state(row[0]) if row else None

    def suspend(self, query_id: str, state: dict[str, Any], public: dict[str, Any]) -> None:
        payload = encode_state(state)
        if len(payload.encode("utf-8")) > 8_000_000:
            raise ResumeConflict("分析断点超过 8 MB，无法安全挂起，请缩小分析范围。")
        response = json.dumps({"question": state["question"], "suspension": public}, ensure_ascii=False)
        with self.connect() as db:
            db.execute("INSERT INTO analysis_checkpoints VALUES (?,?,?) ON CONFLICT(query_id) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at",
                       (query_id, payload, timestamp()))
            db.execute("UPDATE queries SET status='suspended',response=?,error=NULL,updated_at=? WHERE id=?",
                       (response, timestamp(), query_id))
        self.event(query_id, "suspended", {"message": public["message"], "step_id": public.get("failed_step_id")})

    def claim_resume(self, query_id: str, action: str, clarification: str, transform=None) -> str:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT q.status,q.question,c.state FROM queries q LEFT JOIN analysis_checkpoints c ON c.query_id=q.id WHERE q.id=?", (query_id,)).fetchone()
            if row is None or row[0] != "suspended" or not row[2]:
                raise ResumeConflict("当前任务没有可恢复的挂起断点，或已经提交续跑。")
            state = decode_state(row[2])
            if state.get("resume_round", 0) >= state.get("max_resume_rounds", 3):
                raise ResumeConflict("已达到人工续跑次数上限，请终止后调整问题重新查询。")
            if action not in {"retry", "clarify", "replan", "edit"} or (action == "clarify" and not clarification.strip()):
                raise ResumeConflict("请选择重试，或补充有效条件后继续。")
            if transform:
                state = transform(state)
            state["resume_round"] = state.get("resume_round", 0) + 1
            if action == "clarify":
                # Replace rather than append indefinitely; keep bounded input.
                state["clarification"] = clarification.strip()[:1000]
            if action == "replan":
                if not state.get("failed_step_id") or state.get("replan_count", 0) >= 2:
                    raise ResumeConflict("当前断点不支持重规划，或已达到两次重规划上限。")
                state["replan_requested"] = True
            db.execute("UPDATE analysis_checkpoints SET state=?,updated_at=? WHERE query_id=?", (encode_state(state), timestamp(), query_id))
            record = db.execute("SELECT response FROM queries WHERE id=?", (query_id,)).fetchone()
            public_response = json.loads(record[0]) if record and record[0] else {}
            if public_response.get("suspension"):
                public_response["suspension"]["resume_round"] = state["resume_round"]
                if state["resume_round"] >= state.get("max_resume_rounds", 3):
                    public_response["suspension"]["options"] = ["abort"]
            db.execute("UPDATE queries SET status='queued',response=?,error=NULL,updated_at=? WHERE id=?",
                       (json.dumps(public_response, ensure_ascii=False), timestamp(), query_id))
        self.event(query_id, "resume_queued", {"message": "人工确认已收到，将复用已完成结果并恢复未完成步骤。", "action": action})
        return row[1]

    def create(self, query_id: str, question: str, status: str = "queued", options: dict[str, Any] | None = None) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO queries(id,question,status,created_at,updated_at) VALUES (?,?,?,?,?)",
                       (query_id, question, status, timestamp(), timestamp()))
            if options:
                db.execute("UPDATE queries SET request_options=? WHERE id=?", (json.dumps(options, ensure_ascii=False), query_id))
        self.event(query_id, status)

    def event(self, query_id: str, stage: str, detail: dict[str, Any] | None = None) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM queries WHERE id=?", (query_id,)).fetchone():
                return
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM query_events WHERE query_id=?", (query_id,)).fetchone()[0]
            db.execute("INSERT INTO query_events VALUES (?,?,?,?,?)", (query_id, sequence, stage, json.dumps(detail or {}, ensure_ascii=False), timestamp()))

    def status(self, query_id: str, status: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE queries SET status=?,updated_at=? WHERE id=?", (status, timestamp(), query_id))

    def finish(self, query_id: str, status: str, response: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
        # Preserve bounded server results including report evidence. Never silently
        # drop evidence rows; omit the whole payload if its encoded size is excessive.
        payload = json.dumps(response, ensure_ascii=False, default=str) if response else None
        if payload and len(payload.encode()) > 4_000_000:
            payload = json.dumps({"history_payload_omitted": True, "question": (response or {}).get("question"),
                                  "message": "结果超过历史保存上限，请重新查询。"}, ensure_ascii=False)
        with self.connect() as db:
            db.execute("UPDATE queries SET status=?,response=?,error=?,updated_at=? WHERE id=?",
                       (status, payload, json.dumps(error, ensure_ascii=False) if error else None, timestamp(), query_id))
            if status in {"succeeded", "cancelled", "failed", "timed_out"}:
                db.execute("DELETE FROM analysis_checkpoints WHERE query_id=?", (query_id,))
        self.event(query_id, status, error)
        with self.connect() as db:
            db.execute("""DELETE FROM queries WHERE id IN (
              SELECT id FROM queries WHERE favorite=0 AND status NOT IN ('queued','running','cancelling','suspended')
              ORDER BY created_at DESC LIMIT -1 OFFSET ?
            )""", (self.retain,))

    def list_history(self, limit: int = 30, offset: int = 0, favorites: bool = False) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("""
              SELECT id,question,status,created_at,updated_at,favorite,feedback,feedback_note,error
              FROM queries WHERE (?=0 OR favorite=1) ORDER BY created_at DESC LIMIT ? OFFSET ?
            """, (int(favorites), limit, offset))]

    def get(self, query_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM queries WHERE id=?", (query_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        for key in ("response", "error", "request_options"):
            item[key] = json.loads(item[key]) if item[key] else None
        return item

    def events(self, query_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [{**dict(row), "detail": json.loads(row["detail"])} for row in db.execute(
                "SELECT * FROM query_events WHERE query_id=? AND sequence>? ORDER BY sequence LIMIT 500", (query_id, after))]

    def annotate(self, query_id: str, values: dict[str, Any]) -> None:
        allowed = {key: value for key, value in values.items() if key in {"favorite", "feedback", "feedback_note"}}
        if allowed:
            with self.connect() as db:
                db.execute("UPDATE queries SET " + ",".join(f"{key}=?" for key in allowed) + " WHERE id=?", (*allowed.values(), query_id))

    def clear(self, query_id: str | None = None) -> int:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM queries WHERE status NOT IN ('queued','running','cancelling') AND "
                                + ("id=?" if query_id else "favorite=0 AND status!='suspended'"), (query_id,) if query_id else ())
            return cursor.rowcount
