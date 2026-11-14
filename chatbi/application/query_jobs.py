"""Single-worker bounded job runner for the existing synchronous query clients."""
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Callable
from uuid import uuid4

from chatbi.application.progress import QueryControl, QueryStopped, query_control
from chatbi.domain.errors import ChatBIError
from chatbi.infrastructure.query_history import QueryHistory, TERMINAL
from chatbi.application.failure_control import AnalysisSuspended


class QueryJobs:
    def __init__(self, history: QueryHistory, execute: Callable[[str, str], dict[str, Any]], *, capacity: int = 8, timeout: float = 240) -> None:
        self.history, self.execute, self.timeout = history, execute, timeout
        self.queue: Queue[tuple[str, str, float]] = Queue(maxsize=capacity)
        self.events: dict[str, Event] = {}
        self.lock, self.stop = Lock(), Event()
        history.recover()
        self.worker = Thread(target=self._work, name="chatbi-query-worker", daemon=True)
        self.worker.start()

    def submit(self, question: str, options: dict[str, Any] | None = None) -> str:
        query_id = uuid4().hex
        with self.lock:
            if self.stop.is_set() or self.queue.full():
                raise Full()
            self.history.create(query_id, question, options=options)
            self.events[query_id] = Event()
            self.queue.put_nowait((query_id, question, monotonic() + self.timeout))
        return query_id

    def cancel(self, query_id: str) -> bool:
        with self.lock:
            event = self.events.get(query_id)
            item = self.history.get(query_id)
            if item is not None and item["status"] == "suspended":
                self.history.finish(query_id, "cancelled", error={"code": "cancelled", "message": "已终止挂起分析，断点已清理。"})
                return True
            if event is None or item is None or item["status"] in TERMINAL:
                return False
            event.set()
            if item["status"] == "queued":
                self.history.finish(query_id, "cancelled", error={"code": "cancelled", "message": "排队查询已取消，未调用模型或数据库。"})
                return True
            self.history.status(query_id, "cancelling")
            self.history.event(query_id, "cancel_requested", {"message": "已申请取消，等待当前调用返回后停止。"})
            return True

    def resume(self, query_id: str, action: str, clarification: str = "", transform=None) -> str:
        with self.lock:
            if self.stop.is_set() or self.queue.full():
                raise Full()
            question = self.history.claim_resume(query_id, action, clarification, transform)
            self.events[query_id] = Event()
            self.queue.put_nowait((query_id, question, monotonic() + self.timeout))
        return query_id

    def _finish_or_preserve(self, query_id: str, status: str, error: dict[str, Any]) -> None:
        """A failed continuation must not erase the last confirmed checkpoint."""
        state = self.history.checkpoint(query_id)
        if state is not None and status in {"failed", "timed_out"}:
            item = self.history.get(query_id) or {}
            public = dict((item.get("response") or {}).get("suspension", {}))
            public.update(message="续跑未完成，原断点已保留，等待再次确认。", reason=error["message"],
                          resume_round=state.get("resume_round", 0))
            if state.get("resume_round", 0) >= state.get("max_resume_rounds", 3):
                public["options"] = ["abort"]
            self.history.suspend(query_id, state, public)
        else:
            self.history.finish(query_id, status, error=error)

    def _work(self) -> None:
        while not self.stop.is_set():
            try:
                query_id, question, deadline = self.queue.get(timeout=.2)
            except Empty:
                continue
            cancel_event = self.events[query_id]
            try:
                with query_control(QueryControl(cancel_event, deadline, lambda stage, detail: self.history.event(query_id, stage, detail),
                                                self.history.checkpoint(query_id),
                                                (self.history.get(query_id) or {}).get("request_options"))):
                    self.history.status(query_id, "running")
                    self.history.event(query_id, "running")
                    response = self.execute(question, query_id)
                # Serialize completion with cancellation to avoid succeeded after cancel was accepted.
                with self.lock:
                    if cancel_event.is_set():
                        raise QueryStopped()
                    self.history.finish(query_id, "succeeded", response=response)
            except AnalysisSuspended as exc:
                with self.lock:
                    if cancel_event.is_set():
                        self.history.finish(query_id, "cancelled", error={"code": "cancelled", "message": "查询已取消。"})
                    elif monotonic() >= deadline:
                        self._finish_or_preserve(query_id, "timed_out", {"code": "deadline_exceeded", "message": "分析超过总时限。"})
                    else:
                        try:
                            self.history.suspend(query_id, exc.state, exc.public)
                        except Exception:
                            self.history.finish(query_id, "failed", error={"code": "checkpoint_failed", "message": "断点无法保存，请缩小分析范围后重新查询。"})
            except QueryStopped as exc:
                status = "timed_out" if exc.code == "deadline_exceeded" else "cancelled"
                with self.lock:
                    item = self.history.get(query_id)
                    if item and item["status"] not in TERMINAL:
                        self._finish_or_preserve(query_id, status, {"code": exc.code, "message": "分析超过总时限，请缩小范围。" if status == "timed_out" else "查询已取消。"})
            except ChatBIError as exc:
                with self.lock:
                    if cancel_event.is_set():
                        self.history.finish(query_id, "cancelled", error={"code": "cancelled", "message": "查询已取消。"})
                    elif monotonic() >= deadline:
                        self._finish_or_preserve(query_id, "timed_out", {"code": "deadline_exceeded", "message": "分析超过总时限。"})
                    else:
                        self._finish_or_preserve(query_id, "failed", {"code": exc.code.value, "message": exc.public_message})
            except Exception:
                with self.lock:
                    if cancel_event.is_set():
                        self.history.finish(query_id, "cancelled", error={"code": "cancelled", "message": "查询已取消。"})
                    else:
                        self._finish_or_preserve(query_id, "failed", {"code": "internal_error", "message": "查询执行失败，请重试。"})
            finally:
                with self.lock:
                    if self.events.get(query_id) is cancel_event:
                        self.events.pop(query_id, None)
                self.queue.task_done()

    def close(self) -> bool:
        self.stop.set()
        with self.lock:
            for event in self.events.values():
                event.set()
        self.worker.join(timeout=2)
        # Queued records are marked interrupted on next startup; never replay silently.
        return not self.worker.is_alive()
