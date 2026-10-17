"""Browser guest credentials for public deployments; local installs retain user 1."""
import hashlib
import re
import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database import get_session
from backend.models import GuestCredential, User


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def get_current_user_id(request: Request, session: Session = Depends(get_session)) -> int:
    if get_settings().app_mode == "local":
        request.state.user_id = 1
        return 1
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    credential = None
    if scheme.lower() == "bearer" and re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        credential = session.get(GuestCredential, token_digest(token))
    if credential is None or credential.user_id == 1:
        raise HTTPException(401, "访客身份无效或已失效，请保留当前浏览器数据并联系网站管理员。",
                            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})
    request.state.user_id = credential.user_id
    return credential.user_id


def create_guest_session(session: Session) -> dict:
    # Initialization reserves ID 1 for the local dataset. Preserve that invariant
    # even when this helper is used with an otherwise empty database.
    if session.get(User, 1) is None:
        session.add(User(id=1))
        session.flush()
    user = User()
    session.add(user)
    session.flush()
    token = secrets.token_urlsafe(32)
    session.add(GuestCredential(token_hash=token_digest(token), user_id=user.id))
    session.commit()
    return {"token": token, "user_id": user.id}
