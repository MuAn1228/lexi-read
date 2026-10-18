"""Complete backup operations are available only on the local installation."""
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.background import BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database import get_session
from backend.services.backups import SAFETY, export_backup, preview_backup, restore_backup, root


def local_only():
    if get_settings().app_mode != "local":
        raise HTTPException(404, "本机备份功能未在公开模式开放。")


router = APIRouter(prefix="/api/backups", dependencies=[Depends(local_only)])


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(pattern=r"^[a-f0-9]{32}$")
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("请明确确认恢复备份。")
        return value


@router.get("/export")
def export_complete(background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    path = root() / "backup-downloads" / f"lexi-read-{uuid4().hex}.zip"
    export_backup(session.get_bind(), path)
    background_tasks.add_task(path.unlink, missing_ok=True)
    return FileResponse(path, media_type="application/zip", filename=path.name, background=background_tasks,
                        headers={"Cache-Control": "private, no-store"})


@router.post("/preview")
def preview_complete(file: UploadFile):
    try:
        return preview_backup(file)
    finally:
        file.file.close()


@router.post("/restore")
def restore_complete(payload: RestoreRequest, session: Session = Depends(get_session)):
    return restore_backup(session.get_bind(), payload.token)


@router.get("/safety/{filename}")
def safety_backup(filename: str):
    if not SAFETY.fullmatch(filename):
        raise HTTPException(404, "未找到恢复前备份。")
    path = root() / "safety-backups" / filename
    if not path.is_file():
        raise HTTPException(404, "未找到恢复前备份。")
    return FileResponse(path, media_type="application/zip", filename=filename,
                        headers={"Cache-Control": "private, no-store"})
