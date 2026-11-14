"""Request-local progress and cooperative cancellation across synchronous calls."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event
from time import monotonic
from typing import Any, Callable, Iterator


class QueryStopped(BaseException):
    # Vendor error handlers must not translate cancellation into a model fallback.
    def __init__(self, code: str = "cancelled") -> None:
        self.code = code


@dataclass
class QueryControl:
    cancel: Event
    deadline: float
    emit: Callable[[str, dict[str, Any]], None]
    resume_state: dict[str, Any] | None = None
    options: dict[str, Any] | None = None


def resumed_state() -> dict[str, Any] | None:
    control = CURRENT.get()
    return control.resume_state if control else None


def query_options() -> dict[str, Any]:
    override = OPTION_OVERRIDE.get()
    if override is not None:
        return override
    control = CURRENT.get()
    return (control.options or {}) if control else {}


OPTION_OVERRIDE: ContextVar[dict[str, Any] | None] = ContextVar("query_options_override", default=None)


@contextmanager
def analysis_options(options: dict[str, Any]) -> Iterator[None]:
    token = OPTION_OVERRIDE.set(options)
    try:
        yield
    finally:
        OPTION_OVERRIDE.reset(token)


CURRENT: ContextVar[QueryControl | None] = ContextVar("query_control", default=None)


def checkpoint() -> None:
    control = CURRENT.get()
    if control:
        if control.cancel.is_set():
            raise QueryStopped()
        if monotonic() >= control.deadline:
            raise QueryStopped("deadline_exceeded")


def progress(stage: str, **detail: Any) -> None:
    checkpoint()
    control = CURRENT.get()
    if control:
        control.emit(stage, detail)


def remaining_timeout(default: float) -> float:
    checkpoint()
    control = CURRENT.get()
    return min(default, max(.01, control.deadline - monotonic())) if control else default


@contextmanager
def query_control(control: QueryControl) -> Iterator[None]:
    token = CURRENT.set(control)
    try:
        checkpoint()
        yield
        checkpoint()
    finally:
        CURRENT.reset(token)
