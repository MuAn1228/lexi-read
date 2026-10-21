"""A personal Maimemo credential is never shared with public guest accounts."""
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database import get_session
from backend.services import maimemo
from backend.services.maimemo_client import MaimemoClient, MaimemoError


def local_only(response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    if get_settings().app_mode != "local":
        raise HTTPException(404, "个人墨墨同步仅在本地版开放。")


router = APIRouter(prefix="/api/integrations/maimemo", dependencies=[Depends(local_only)])
_reading = Lock()


def token() -> str:
    value = get_settings().maimemo_api_token.get_secret_value().strip()
    if not value:
        raise HTTPException(503, "请在本机 .env 配置 MAIMEMO_API_TOKEN 并重启词间。")
    return value


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    include_planned: StrictBool = False


@router.get("/status")
def connection_status(session: Session = Depends(get_session)):
    return maimemo.status(session, bool(get_settings().maimemo_api_token.get_secret_value().strip()))


@router.get("/sources")
def imported_sources(session: Session = Depends(get_session)):
    return maimemo.source_rows(session)


@router.post("/preview")
def preview_records():
    credential = token()
    if not _reading.acquire(blocking=False):
        raise HTTPException(409, "正在读取墨墨数据，请等待当前请求完成。")
    try:
        try:
            snapshot = MaimemoClient(credential).fetch_records()
        except MaimemoError as exc:
            raise HTTPException(exc.status_code, str(exc)) from None
        return maimemo.save_preview(credential, snapshot)
    finally:
        _reading.release()


@router.post("/import")
def import_records(payload: ImportRequest, session: Session = Depends(get_session)):
    snapshot = maimemo.load_preview(payload.preview_id, token())
    return maimemo.import_preview(session, snapshot, payload.include_planned)
