from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Employee, User, UserSession, Company, Trustee
from backend.app.schemas import LoginRequest, LoginResponse, ChangePasswordRequest
from backend.app.services.audit import record_audit_event
from backend.app.auth import (
    hash_password, verify_password, create_session, get_current_user,
    is_account_locked, register_failed_login, register_successful_login,
    cleanup_expired_sessions,
)

router = APIRouter()


# ===================================================================
# AUTH
# ===================================================================

@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    # ניקוי session-ים שפגו: אין scheduler בפרויקט, אז נקודת הכניסה הבטוחה ביותר
    # שתמיד נקראת היא ההתחברות עצמה (אותו רעיון כמו מרכז ההתראות - מחושב על
    # קריאה ולא נשמר בנפרד).
    cleanup_expired_sessions(db)

    user = db.query(User).filter(User.username == payload.username).first()

    # נעילה נבדקת *לפני* אימות הסיסמה: משתמש נעול לא אמור לקבל עוד ניסיון בכלל,
    # גם אם הזין את הסיסמה הנכונה במקרה.
    if user and is_account_locked(user):
        raise HTTPException(
            status_code=423,
            detail=f"Account locked until {user.locked_until.isoformat()} due to repeated failed logins",
        )

    if not user or not user.is_active or not verify_password(payload.password, user.password_hash, user.password_salt):
        if user and user.is_active:
            register_failed_login(db, user)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    register_successful_login(db, user)
    token = create_session(db, user)

    display_name = user.username
    if user.employee_id:
        emp = db.query(Employee).filter(Employee.employee_id == user.employee_id).first()
        if emp:
            display_name = f"{emp.first_name} {emp.last_name}"
    elif user.company_id:
        comp = db.query(Company).filter(Company.company_id == user.company_id).first()
        if comp:
            display_name = comp.name
    elif user.trustee_id:
        trustee_row = db.query(Trustee).filter(Trustee.trustee_id == user.trustee_id).first()
        if trustee_row:
            display_name = trustee_row.name

    return LoginResponse(
        token=token,
        role=user.role.value if hasattr(user.role, "value") else user.role,
        display_name=display_name,
        company_id=user.company_id,
        trustee_id=user.trustee_id,
        employee_id=user.employee_id,
        must_change_password=user.must_change_password,
    )


@router.post("/auth/change-password")
def change_password(payload: ChangePasswordRequest, authorization: str = Header(None),
                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """זמין תמיד דרך get_current_user בלבד (לא require_roles) - אחרת משתמש עם
    must_change_password=True לא היה יכול להגיע לכאן כדי לתקן את זה."""
    if not verify_password(payload.current_password, current_user.password_hash, current_user.password_salt):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current one")

    pw_hash, salt = hash_password(payload.new_password)
    current_user.password_hash, current_user.password_salt = pw_hash, salt
    current_user.must_change_password = False

    # מבטל כל session אחר של המשתמש הזה - אם הסיבה לשינוי הייתה חשד לחשיפת
    # הסיסמה, ה-session שנחשף לא אמור להישאר תקף. ה-session הנוכחי (זה שביצע
    # את הבקשה הזו) לא מבוטל, אחרת המשתמש היה מנותק מיד אחרי שינוי מוצלח.
    current_token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    query = db.query(UserSession).filter(UserSession.user_id == current_user.user_id)
    if current_token:
        query = query.filter(UserSession.token != current_token)
    query.delete(synchronize_session=False)

    record_audit_event(db, "User", current_user.user_id, "PASSWORD_CHANGE", current_user.user_id)
    db.commit()
    return {"status": "password_changed"}


@router.post("/auth/logout")
def logout(authorization: str = Header(None), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        db.query(UserSession).filter(UserSession.token == token).delete()
        db.commit()
    return {"status": "logged_out"}


@router.get("/auth/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "user_id": current_user.user_id,
        "username": current_user.username,
        "role": current_user.role,
        "company_id": current_user.company_id,
        "trustee_id": current_user.trustee_id,
        "employee_id": current_user.employee_id,
    }
