import hashlib
import hmac
import secrets
import string
from datetime import timedelta

from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app import models
from backend.app.types import utcnow

# נעילת חשבון אחרי כשלונות חוזרים (v0.5.1 - patch אבטחה). קבועים ולא הגדרת admin:
# זו החלטת מוצר שמרנית, לא כלל מס - מותר לשנות בלי אימות חיצוני.
MAX_FAILED_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)

TEMP_PASSWORD_ALPHABET = string.ascii_letters + string.digits


def generate_temporary_password(length: int = 14) -> str:
    """סיסמה חד-פעמית מוגרלת, לא קבועה כמו ה-``Welcome123!`` הקודם.

    מוחזרת פעם אחת בתגובת ה-API שיוצרת את המשתמש ולא נשמרת בשום מקום אחר (רק
    ה-hash שלה) - האדמין חייב למסור אותה לעובד עכשיו, ואי אפשר לשלוף אותה שוב.
    """
    return "".join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(length))


def is_account_locked(user: "models.User") -> bool:
    return user.locked_until is not None and user.locked_until > utcnow()


def register_failed_login(db: Session, user: "models.User") -> None:
    """מעלה את המונה, ונועל את החשבון אם הגיע לסף. לא מאפסים את המונה עם הזמן -
    רק כניסה מוצלחת מאפסת אותו, כדי שניסיונות מפוזרים על פני זמן עדיין ייספרו."""
    user.failed_login_attempts += 1
    if user.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
        user.locked_until = utcnow() + LOCKOUT_DURATION
    db.commit()


def register_successful_login(db: Session, user: "models.User") -> None:
    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()


def cleanup_expired_sessions(db: Session) -> None:
    """מוחק session-ים שפגו. נקרא על כל login ולא כ-job נפרד: אין scheduler
    בפרויקט (אותה החלטה שכבר התקבלה במרכז ההתראות - מחושב על קריאה ולא
    באחסון נפרד), ונקודת הכניסה היחידה שבטוח נקראת הרבה היא ההתחברות."""
    db.query(models.UserSession).filter(
        models.UserSession.expires_at < utcnow()
    ).delete(synchronize_session=False)
    db.commit()


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
    # 30 יום לבקשת המשתמש (היה 12 שעות) - כדי שלא יצטרך להתחבר מחדש כל בדיקה
    # ידנית. אין רענון/rotation לטוקן במערכת הזו, אז זו הרחבה מודעת של חלון
    # הזמן שטוקן גנוב/דלוף נשאר תקף - קיבל אישור מפורש, לא ברירת מחדל.
    session = models.UserSession(
        token=token,
        user_id=user.user_id,
        expires_at=utcnow() + timedelta(days=30),
    )
    db.add(session)
    db.commit()
    return token


def get_current_user(authorization: str = Header(None), db: Session = Depends(get_db)) -> models.User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    token = authorization.split(" ", 1)[1]

    session = db.query(models.UserSession).filter(models.UserSession.token == token).first()
    if not session or session.expires_at < utcnow():
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    user = db.query(models.User).filter(models.User.user_id == session.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    return user


def require_roles(*roles: "models.UserRole"):
    def _checker(current_user: models.User = Depends(get_current_user)) -> models.User:
        if current_user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions for this action")
        if current_user.must_change_password:
            # חוסם כל endpoint עסקי (admin/trustee/employee) עד שהסיסמה החד-פעמית
            # הוחלפה. /search ו-/notifications עוברים דרך get_current_user בלבד
            # ולא דרך require_roles, ולכן *לא* חסומים כרגע - ראו R-051 ב-QA_TESTBOOK.
            raise HTTPException(
                status_code=403,
                detail="Password change required before continuing - call POST /auth/change-password",
            )
        return current_user
    return _checker
