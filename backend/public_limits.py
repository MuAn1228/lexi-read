"""Persistent attempt budgets and bounded expensive work for one public worker."""
from datetime import datetime, timezone
import hashlib
from threading import BoundedSemaphore

from fastapi import Depends, HTTPException, Request
from sqlalchemy import Integer, String, delete
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from backend.auth import get_current_user_id
from backend.config import get_settings
from backend.database import get_session
from backend.models import Base


class PublicUsage(Base):
    __tablename__ = "public_usage"
    bucket: Mapped[str] = mapped_column(String(180), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False, index=True)


_llm_slots = BoundedSemaphore(2)
_ocr_slots = BoundedSemaphore(1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def consume(session: Session, limits: list[tuple[str, int, int]]) -> None:
    """Atomically reserve every bucket or none; commits independently of learning data.

    A failed provider/validation attempt still counts. Counters survive restarts.
    The separate transaction avoids committing or rolling back caller changes.
    """
    now = int(_now().timestamp())
    with Session(bind=session.get_bind()) as budget, budget.begin():
        budget.execute(delete(PublicUsage).where(PublicUsage.expires_at < now - 86400))
        for bucket, limit, expiry in limits:
            statement = insert(PublicUsage).values(bucket=bucket, count=1, expires_at=expiry)
            statement = statement.on_conflict_do_update(
                index_elements=[PublicUsage.bucket], set_={"count": PublicUsage.count + 1},
                where=PublicUsage.count < limit).returning(PublicUsage.count)
            if budget.scalar(statement) is None:
                raise HTTPException(429, "今日使用额度或当前时段额度已用完，请稍后再试。",
                                    headers={"Retry-After": str(max(1, expiry - now))})


def check_signup_budget(request: Request, session: Session) -> None:
    settings = get_settings()
    if settings.app_mode != "public":
        return
    now = int(_now().timestamp())
    day, hour = now // 86400, now // 3600
    # Store a rotating hash rather than raw client addresses. Forwarded headers
    # are handled by the hosting proxy/ASGI server, never trusted here directly.
    address = request.client.host if request.client else "unknown"
    fingerprint = hashlib.sha256(f"{day}:{address}".encode()).hexdigest()
    consume(session, [(f"signup:{day}:global", settings.public_signup_daily_limit, (day + 1) * 86400),
                      (f"signup:{hour}:ip:{fingerprint}", settings.public_signup_hourly_limit,
                       (hour + 1) * 3600)])


def _reserve(session: Session, user_id: int, kind: str) -> None:
    settings = get_settings()
    day = int(_now().timestamp()) // 86400
    expiry = (day + 1) * 86400
    consume(session, [(f"{kind}:{day}:global", getattr(settings, f"public_{kind}_daily_limit"), expiry),
                      (f"{kind}:{day}:user:{user_id}", getattr(settings, f"public_{kind}_user_daily_limit"), expiry)])


def _work_budget(session: Session, user_id: int, kind: str, slots: BoundedSemaphore):
    if get_settings().app_mode != "public":
        yield
        return
    if not slots.acquire(blocking=False):
        raise HTTPException(429, "服务正在处理其他请求，请稍后重试。", headers={"Retry-After": "10"})
    try:
        _reserve(session, user_id, kind)
        yield
    finally:
        slots.release()


def require_llm_budget(user_id: int = Depends(get_current_user_id), session: Session = Depends(get_session)):
    yield from _work_budget(session, user_id, "llm", _llm_slots)


def require_ocr_budget(user_id: int = Depends(get_current_user_id), session: Session = Depends(get_session)):
    yield from _work_budget(session, user_id, "ocr", _ocr_slots)
