"""Bounded SSE delivery around the existing, synchronous AI business services.

The worker owns its database session. Only validated results cross this boundary;
previews are ephemeral snapshots extracted from actual provider deltas.
"""
import asyncio
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
from threading import Condition, Event, Lock
from time import monotonic
from typing import Callable

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.services.progressive_json import progressive_text


class StreamCancelled(BaseException):
    """Cancellation must bypass business services' ordinary failure fallbacks."""


_current = ContextVar("ai_stream_observer", default=None)
_workers: set[asyncio.Task] = set()


def current_observer():
    return _current.get()


def check_cancelled():
    observer = current_observer()
    if observer is not None:
        observer.check()


@contextmanager
def stream_context(observer):
    token = _current.set(observer)
    try:
        observer.check()
        yield observer
    finally:
        _current.reset(token)


@contextmanager
def completed_write():
    """Serialize the tiny final cache write against disconnect cancellation."""
    observer = current_observer()
    if observer is None:
        yield
    else:
        with observer.write_lock:
            observer.check()
            yield


class _Mailbox:
    def __init__(self):
        self.condition = Condition()
        self.items = deque()

    def put(self, event, data):
        with self.condition:
            # At most one draft snapshot waits for delivery. Statuses and the
            # terminal result are tiny and bounded independently of token rate.
            if event == "preview":
                self.items = deque(item for item in self.items if item[0] != "preview")
            while len(self.items) >= 8:
                self.items.popleft()
            self.items.append((event, data))
            self.condition.notify()

    def take(self, timeout=.2):
        with self.condition:
            if not self.items:
                self.condition.wait(timeout)
            return self.items.popleft() if self.items else None


@dataclass
class StreamObserver:
    kind: str
    emit: Callable
    preview_enabled: bool = True
    cancelled: Event = field(default_factory=Event)
    write_lock: Lock = field(default_factory=Lock)
    transport_lock: Lock = field(default_factory=Lock)
    response: object | None = None
    last_preview: str = ""
    last_preview_at: float = 0

    def check(self):
        if self.cancelled.is_set():
            raise StreamCancelled()

    def status(self, phase):
        self.check()
        self.emit("status", {"phase": phase})

    def preview(self, content, *, force=False):
        self.check()
        if not self.preview_enabled:
            return
        now = monotonic()
        if not force and now - self.last_preview_at < .06:
            return
        text = progressive_text(content, self.kind)
        if text and text != self.last_preview:
            self.last_preview, self.last_preview_at = text, now
            self.emit("preview", {"text": text})

    @contextmanager
    def transport(self, response):
        with self.transport_lock:
            self.check()
            self.response = response
        try:
            yield
            self.check()
        except Exception:
            # Closing a blocked socket surfaces as ReadError on some systems.
            # Convert it back to cancellation before a service can interpret it
            # as an ordinary provider failure and continue its business work.
            self.check()
            raise
        finally:
            with self.transport_lock:
                self.response = None

    def cancel(self):
        # The event is set before closing the socket, so even a racing final
        # chunk cannot validate, write a cache or publish a completed result.
        self.cancelled.set()
        with self.write_lock:
            pass
        with self.transport_lock:
            response = self.response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _encode(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _http_error(exc):
    message = exc.detail if isinstance(exc.detail, str) and len(exc.detail) <= 500 else "请求暂时无法完成，请稍后重试。"
    headers = {key: value for key, value in (exc.headers or {}).items()
               if key.lower() in {"retry-after", "www-authenticate", "cache-control"}}
    status = exc.status_code if 400 <= exc.status_code <= 599 else 500
    return HTTPException(status, message, headers=headers)


async def stream_response(request: Request, bind, operation: Callable, *, kind: str,
                          preview_enabled=True):
    mailbox = _Mailbox()
    observer = StreamObserver(kind, mailbox.put, preview_enabled=preview_enabled)

    def work():
        try:
            with Session(bind=bind, expire_on_commit=False) as session, stream_context(observer):
                result = operation(session)
                observer.check()
                mailbox.put("result", result.model_dump(mode="json"))
        except StreamCancelled:
            pass
        except HTTPException as exc:
            mailbox.put("http_error", _http_error(exc))
        except Exception:
            # Provider payloads, addresses, keys and exception text never leave
            # the server, including failures outside the normal service guards.
            mailbox.put("error", {"message": "AI 服务暂时不可用，请稍后重试。", "status": 500})

    task = asyncio.create_task(asyncio.to_thread(work))
    _workers.add(task)
    task.add_done_callback(_workers.discard)

    def cancel():
        # Set this synchronously before yielding control so a worker racing the
        # disconnect cannot save its response while socket cleanup is queued.
        observer.cancelled.set()
        cleanup = asyncio.create_task(asyncio.to_thread(observer.cancel))
        _workers.add(cleanup)
        cleanup.add_done_callback(_workers.discard)

    handed_off = False
    try:
        # Service authorization, input/context checks and budget reservation run
        # before the first provider status. Their errors retain HTTP semantics.
        first = None
        while first is None:
            if await request.is_disconnected():
                raise HTTPException(499, "请求已取消。")
            first = await asyncio.to_thread(mailbox.take)
        if first[0] == "http_error":
            raise first[1]

        async def events():
            item = first
            heartbeat = monotonic()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    if item is not None:
                        event, data = item
                        if event == "http_error":
                            event, data = "error", {"message": str(data.detail), "status": data.status_code}
                        yield _encode(event, data)
                        if event in {"result", "error"}:
                            break
                    elif monotonic() - heartbeat >= 10:
                        yield ": keep-alive\n\n"
                        heartbeat = monotonic()
                    item = await asyncio.to_thread(mailbox.take)
            finally:
                # Closing an HTTP transport can block briefly: never do it on
                # the event loop, even during ASGI disconnect cancellation.
                cancel()

        handed_off = True
        return StreamingResponse(events(), media_type="text/event-stream", headers={
            "Cache-Control": "private, no-store", "X-Accel-Buffering": "no"})
    finally:
        if not handed_off:
            cancel()
