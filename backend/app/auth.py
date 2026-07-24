import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app import models


def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    digest, _ = hash_password(password, salt)
    return hmac.compare_digest(digest, password_hash)


def create_session(db: Session, user: models.User) -> str:
    token = secrets.token_urlsafe(32)
    session = models.UserSession(
        token=token,
        user_id=user.user_id,
        expires_at=datetime.utcnow() + timedelta(hours=12),
    )
    db.add(session)
    db.commit()
    return token


def get_current_user(authorization: str = Header(None), db: Session = Depends(get_db)) -> models.User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    token = authorization.split(" ", 1)[1]

    session = db.query(models.UserSession).filter(models.UserSession.token == token).first()
    if not session or session.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    user = db.query(models.User).filter(models.User.user_id == session.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    return user


def require_roles(*roles: "models.UserRole"):
    def _checker(current_user: models.User = Depends(get_current_user)) -> models.User:
        if current_user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions for this action")
        return current_user
    return _checker
