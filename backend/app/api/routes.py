import json
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Response
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.types import utcnow, ensure_utc, business_today
from backend.app.models import (
    Employee, EmployeeStatus, OptionPool, Grant, StockPricesHistory,
    Trustee, VestingSchedule, User, UserRole, UserSession, ExerciseRequest, ExerciseRequestStatus,
    Company, AuditLog, NotificationPreference, NotificationDismissal,
    NOTIFICATION_DEFAULT_LEAD_DAYS, LedgerOwnership, LEDGER_AGGREGATE_TYPES,
    Document, DocumentStatus, DOCUMENT_TEMPLATE_TYPES, generate_uuid,
)
from backend.app.schemas import (
    EmployeeStatusUpdate, ExerciseSimulationRequest, ExerciseSimulationResponse,
    CreateGrantRequest, CreateGrantResponse, LoginRequest, LoginResponse,
    ChangePasswordRequest,
    EmployeeCreateRequest, EmployeeUpdateRequest, EmployeeOut, EmployeeCreateResponse,
    CompanyUpdateRequest, CompanyOut, GrantOut, PoolOut,
    TrusteePortfolioItem, ExerciseRequestCreate, ExerciseRequestReview, ExerciseRequestOut,
    AuditLogOut, SearchResultItem,
    NotificationFeedOut, NotificationCountOut, NotificationPreferencesOut,
    NotificationPreferencesUpdate, LedgerEventOut, LedgerProjectionOut,
    VestingPauseRequest, VestingPauseResponse,
    GenerateDocumentRequest, DocumentOut,
)
from backend.app.services.engine import (
    DeterministicESOPEngine, MissingVestingScheduleError, shift_months,
)
from backend.app.services.tax_engine import TaxCalculationEngine, MissingTaxRuleError
from backend.app.services.search_engine import SearchEngine
from backend.app.services import notifications as notif
from backend.app.services.audit import record_audit_event
from backend.app.services.ledger import append_event, events_for, project, record_ownership
from backend.app.services.documents import (
    TEMPLATE_BUILDERS, MissingDocumentDataError, DocumentRenderingError, DOCUMENT_STORE_DIR,
)
from backend.app.services.document_access import assert_document_access
from backend.app.services.document_status import assert_is_current_version, assert_transition_allowed
from backend.app.auth import (
    hash_password, verify_password, create_session, get_current_user, require_roles,
    generate_temporary_password, is_account_locked, register_failed_login,
    register_successful_login, cleanup_expired_sessions,
)
from backend.app.version import get_version

router = APIRouter()


# ===================================================================
# ולידציות משותפות - יושבות כאן ולא בכל endpoint בנפרד, כי אותה בדיקה
# חסרה קודם בשני נתיבי אישור שונים (admin ו-trustee) ובנתיב ההגשה.
# ===================================================================

# גיל מינימלי להענקת אופציות. ⚠️ ברירת מחדל שמרנית ברמת המערכת ולא כלל מאומת:
# כשירות משפטית של קטין לחתום על כתב הענקה היא שאלה משפטית שטרם אומתה מול מקור.
# הכיוון נבחר כך שהמערכת *חוסמת* במקום להעניק בשקט מענק שאולי אינו אכיף.
MINIMUM_GRANT_AGE_YEARS = 18


def _vested_at(grant: Grant, on_date: date) -> float:
    """הבשלה בתאריך נתון, עם עצירה ביום העזיבה. נקודת הכניסה היחידה שאמורה
    לשמש את ה-endpoints, כדי שאף נתיב לא ישכח את ה-cutoff."""
    cutoff = DeterministicESOPEngine.vesting_cutoff_date(grant.employee, on_date)
    return DeterministicESOPEngine.calculate_vested_options(grant, grant.vesting_schedule, cutoff)


def _vested_or_conflict(grant: Grant, on_date: date) -> float:
    """כמה הבשיל, או 409 כשאין לוח הבשלה בכלל.

    409 ולא 500: המענק קיים ותקין, מה שחסר הוא נתון שבלעדיו אי אפשר להחליט.
    התשובה הנכונה היא "לא ניתן להכריע", לא "0 הבשילו".
    """
    try:
        return _vested_at(grant, on_date)
    except MissingVestingScheduleError:
        raise HTTPException(
            status_code=409,
            detail=(f"Grant {grant.grant_id} has no vesting schedule - the vested amount "
                    "cannot be determined. Attach a vesting schedule before proceeding."),
        )


def _company_id_of_grant(db: Session, grant: Grant) -> "str | None":
    """גוזר company_id דרך grant.pool_id - Grant עצמו לא מחזיק את זה ישירות.
    אותו דפוס בדיוק כמו backfill_ledger._company_id_of_pool, לצורך רישום
    ledger_ownership על ישויות שנוצרות דרך grant (ExerciseRequest וכו')."""
    pool = db.query(OptionPool).filter(OptionPool.pool_id == grant.pool_id).first()
    return pool.company_id if pool else None


def _options_committed(db: Session, grant_id: str, statuses: tuple,
                        exclude_request_id: str = None) -> float:
    """סך האופציות שכבר "תפוסות" ע"י בקשות אחרות על אותו מענק."""
    q = db.query(ExerciseRequest).filter(
        ExerciseRequest.grant_id == grant_id,
        ExerciseRequest.status.in_(statuses),
    )
    if exclude_request_id:
        q = q.filter(ExerciseRequest.request_id != exclude_request_id)
    return float(sum(r.options_requested for r in q.all()))


def _assert_request_approvable(db: Session, req: ExerciseRequest, grant: Grant) -> None:
    """שלוש בדיקות שקודם לא נעשו באף אחד משני נתיבי האישור.

    כולן חוסמות ולא מתריעות: אישור שגוי כאן הוא מימוש שהחברה כבר אישרה, ואי אפשר
    "לתקן אותו בדוח" בדיעבד.
    """
    if req.status != ExerciseRequestStatus.PENDING:
        raise HTTPException(status_code=409,
                            detail=f"Request is already {req.status.value}; only PENDING can be reviewed")

    today = business_today()
    vested = _vested_or_conflict(grant, today)

    # רק APPROVED נחשב "תפוס" בשלב האישור - בקשות PENDING אחרות עדיין לא אושרו,
    # והן נחסמות בתורן כשיגיע תורן (וזה מה שמונע את אישור שתי הבקשות החופפות).
    already_approved = _options_committed(
        db, grant.grant_id, (ExerciseRequestStatus.APPROVED,), exclude_request_id=req.request_id)

    if req.options_requested + already_approved > vested:
        raise HTTPException(
            status_code=400,
            detail=(f"Cannot approve {req.options_requested:.0f} options: only {vested:.0f} vested, "
                    f"and {already_approved:.0f} already approved on this grant"),
        )

    if grant.trustee_id:
        is_met, end_date = DeterministicESOPEngine.check_trustee_holding_period(grant, today)
        if not is_met:
            # חסימה מוחלטת ולא אזהרה: שחרור מוקדם מנאמנות מפיל את המענק ממסלול
            # רווח הון להכנסת עבודה. זרימת "שחרור מוקדם ביודעין" היא פיצ'ר נפרד
            # שדורש אימות כלל מס לפני שיימומש - ולא ברירת מחדל שקטה.
            raise HTTPException(
                status_code=400,
                detail=(f"Trustee holding period (Section 102) is not met until {end_date}; "
                        "approving before that date forfeits capital-gains treatment"),
            )


def _decide_exercise_request(db: Session, req: ExerciseRequest, payload: ExerciseRequestReview,
                              actor_user_id: str) -> ExerciseRequest:
    """נקודת כתיבה אחת ויחידה לשני נתיבי האישור (admin+trustee) - v0.6.0.

    קודם כל נתיב שיכפל את השינוי בעצמו, וזה בדיוק דפוס P3 (QA_TESTBOOK.md):
    ולידציה/לוגיקה שקיימת בנתיב אחד וחסרה/שונה בשני. חייבת להיקרא בתוך אותה
    טרנזקציה כמו _assert_request_approvable (אם approve=True) ולא אחריה בנפרד -
    אחרת יש חלון TOCTOU בין הבדיקה לכתיבה (ראו סקירת האבטחה לתכנון v0.6.0)."""
    req.status = ExerciseRequestStatus.APPROVED if payload.approve else ExerciseRequestStatus.REJECTED
    req.reviewed_by_user_id = actor_user_id
    req.reviewed_at = utcnow()
    req.review_notes = payload.notes
    record_audit_event(db, "ExerciseRequest", req.request_id,
                        "APPROVE" if payload.approve else "REJECT", actor_user_id,
                        before={"status": "PENDING"}, after={"status": req.status.value, "notes": payload.notes})
    append_event(db, event_type="EXERCISE_REQUEST_DECIDED", aggregate_type="ExerciseRequest",
                aggregate_id=req.request_id,
                payload={"status": req.status.value, "notes": payload.notes},
                effective_date=business_today(), actor_user_id=actor_user_id)
    return req


@router.get("/search", response_model=List[SearchResultItem])
def search(q: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """חיפוש חופשי חכם (fuzzy) - כל תפקיד מחפש רק בתוך תחום ההרשאה שלו."""
    if current_user.role == UserRole.COMPANY_ADMIN:
        results = SearchEngine.search_for_admin(db, current_user.company_id, q)
    elif current_user.role == UserRole.TRUSTEE:
        results = SearchEngine.search_for_trustee(db, current_user.trustee_id, q)
    else:
        results = SearchEngine.search_for_employee(db, current_user.employee_id, q)

    return [SearchResultItem(entity_type=r.entity_type, entity_id=r.entity_id,
                              title=r.title, subtitle=r.subtitle, score=round(r.score, 3))
            for r in results]


# ===================================================================
# NOTIFICATIONS - מחושבות על קריאה, לא נשמרות. ראו services/notifications.py
# ===================================================================

def _feed_for(current_user: User, db: Session) -> "notif.NotificationFeed":
    """הפניה לפי תפקיד. הסקופ נאכף בתוך services/notifications.py, שם הוא
    נקבע מ-current_user בלבד - אין כאן פרמטר שהלקוח יכול לדרוס."""
    if current_user.role == UserRole.COMPANY_ADMIN:
        return notif.for_admin(db, current_user.company_id, current_user.user_id)
    if current_user.role == UserRole.TRUSTEE:
        return notif.for_trustee(db, current_user.trustee_id, current_user.user_id)
    return notif.for_employee(db, current_user.employee_id, current_user.user_id)


@router.get("/notifications", response_model=NotificationFeedOut)
def list_notifications(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    feed = _feed_for(current_user, db)
    return NotificationFeedOut(items=[vars(i) for i in feed.items],
                                degraded_entities=feed.degraded_entities, total=feed.total)


@router.get("/notifications/unread-count", response_model=NotificationCountOut)
def notifications_unread_count(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # מדווח את הסך האמיתי ולא את הפיד הקטוע - אחרת התקרה נראית כמו העובדה.
    return NotificationCountOut(count=_feed_for(current_user, db).total)


@router.post("/notifications/{key:path}/dismiss", status_code=204)
def dismiss_notification(key: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """idempotent: נשען על ה-unique index ברמת ה-DB במקום check-then-insert,
    שהוא race שמייצר כפילויות בדיוק בלחיצה כפולה."""
    db.add(NotificationDismissal(user_id=current_user.user_id, notification_key=key))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return Response(status_code=204)


@router.get("/notifications/preferences", response_model=NotificationPreferencesOut)
def get_notification_preferences(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    effective = notif._effective_preferences(db, current_user.user_id)
    return NotificationPreferencesOut(preferences=[
        {"rule": rule, "enabled": cfg["enabled"], "lead_days": cfg["lead_days"]}
        for rule, cfg in effective.items()
    ])


@router.put("/notifications/preferences", response_model=NotificationPreferencesOut)
def update_notification_preferences(payload: NotificationPreferencesUpdate,
                                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    for item in payload.preferences:
        if item.rule not in NOTIFICATION_DEFAULT_LEAD_DAYS:
            raise HTTPException(status_code=400, detail=f"Unknown notification rule: {item.rule}")
        if item.lead_days < 0:
            raise HTTPException(status_code=400, detail="lead_days must not be negative")

    for item in payload.preferences:
        row = (db.query(NotificationPreference)
               .filter(NotificationPreference.user_id == current_user.user_id,
                       NotificationPreference.rule == item.rule).first())
        if row:
            row.enabled, row.lead_days = item.enabled, item.lead_days
        else:
            db.add(NotificationPreference(user_id=current_user.user_id, rule=item.rule,
                                           enabled=item.enabled, lead_days=item.lead_days))
    db.commit()
    return get_notification_preferences(current_user, db)


@router.get("/version")
def read_version():
    """גרסת המערכת - ציבורי, בלי אימות, כדי ששלושת הפורטלים יוכלו להציג אותה.
    נקרא מהקובץ מחדש בכל בקשה כדי שעדכון גרסה ישתקף מיד, בלי restart לשרת."""
    return {"version": get_version()}


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


# ===================================================================
# COMPANY ADMIN PORTAL - Employees CRUD
# ===================================================================

@router.get("/admin/employees", response_model=List[EmployeeOut])
def list_employees(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    # סקופ לפי החברה של המשתמש. קודם הוחזרו כל העובדים בכל החברות (דליפה חוצת-לקוחות).
    return db.query(Employee).filter(Employee.company_id == current_user.company_id).all()


@router.post("/admin/employees", response_model=EmployeeCreateResponse)
def create_employee(payload: EmployeeCreateRequest, current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    existing = db.query(Employee).filter(Employee.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Employee with this email already exists")

    emp = Employee(
        company_id=current_user.company_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        country_code=payload.country_code,
        status=EmployeeStatus.ACTIVE,
        hire_date=payload.hire_date,
        birth_date=payload.birth_date,
    )
    db.add(emp)
    db.flush()

    # v0.6.0: אירוע בסיס לעובד חדש - בלי זה, עובד שנוצר *אחרי* backfill_ledger.py
    # לא יהיה לו שום אירוע לקפל, ו-EMPLOYEE_STATUS_CHANGED עתידי (עזיבה וכו')
    # ייפול על state=None בלי אפקט (ראו project_employee).
    record_ownership(db, aggregate_id=emp.employee_id, aggregate_type="Employee",
                     company_id=emp.company_id, employee_id=emp.employee_id)
    append_event(db, event_type="EMPLOYEE_STATE_ESTABLISHED", aggregate_type="Employee",
                aggregate_id=emp.employee_id,
                payload={"status": EmployeeStatus.ACTIVE.value, "termination_date": None},
                effective_date=emp.hire_date, actor_user_id=current_user.user_id)

    # פרובייז אוטומטי של חשבון כניסה עם סיסמה חד-פעמית מוגרלת - לא הקבועה
    # Welcome123! שהייתה זהה לכל עובד חדש בכל חברה. must_change_password=True
    # חוסם כל endpoint עסקי (require_roles) עד שהעובד יחליף אותה בעצמו.
    temp_password = generate_temporary_password()
    pw_hash, salt = hash_password(temp_password)
    new_user = User(username=emp.email, password_hash=pw_hash, password_salt=salt,
                    role=UserRole.EMPLOYEE, employee_id=emp.employee_id,
                    must_change_password=True)
    db.add(new_user)

    db.commit()
    db.refresh(emp)
    return EmployeeCreateResponse(**EmployeeOut.model_validate(emp).model_dump(),
                                  temporary_password=temp_password)


@router.put("/admin/employees/{employee_id}", response_model=EmployeeOut)
def update_employee(employee_id: str, payload: EmployeeUpdateRequest,
                     current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.employee_id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if emp.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot modify an employee outside your company")

    before = {"first_name": emp.first_name, "last_name": emp.last_name, "email": emp.email,
              "country_code": emp.country_code, "birth_date": emp.birth_date}
    for field in ["first_name", "last_name", "email", "country_code", "birth_date"]:
        value = getattr(payload, field)
        if value is not None:
            setattr(emp, field, value)

    record_audit_event(db, "Employee", employee_id, "UPDATE", current_user.user_id,
                        before=before, after={"first_name": emp.first_name, "last_name": emp.last_name,
                                              "email": emp.email, "country_code": emp.country_code,
                                              "birth_date": emp.birth_date})
    db.commit()
    db.refresh(emp)
    return emp


@router.delete("/admin/employees/{employee_id}")
def delete_employee(employee_id: str, current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.employee_id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if emp.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot delete an employee outside your company")

    grants_count = db.query(Grant).filter(Grant.employee_id == employee_id).count()

    if grants_count == 0:
        # מעולם לא קיבל grant - מותר למחוק לגמרי (hard delete), כולל חשבון הכניסה שלו.
        orphan_user_ids = [u.user_id for u in db.query(User.user_id).filter(User.employee_id == employee_id).all()]
        if orphan_user_ids:
            db.query(UserSession).filter(UserSession.user_id.in_(orphan_user_ids)).delete(synchronize_session=False)
            db.query(User).filter(User.employee_id == employee_id).delete()
        record_audit_event(db, "Employee", employee_id, "HARD_DELETE", current_user.user_id,
                            before={"email": emp.email, "status": emp.status})
        db.delete(emp)
        db.commit()
        return {"employee_id": employee_id, "deleted": "hard"}
    else:
        # יש היסטוריית grants - אסור למחוק, רק מסמנים TERMINATED.
        before_status = emp.status
        emp.status = EmployeeStatus.TERMINATED
        # חייב להיות *אותו* שעון כמו check_post_termination_exercise_window, כי
        # הדדליין נגזר מכאן: termination_date + window_days. עד v0.9.1 זה היה
        # date.today() (שעון המארח) בעוד הבדיקה רצה על שעון אחר - הן הסכימו רק
        # כל עוד המארח מוגדר לישראל, כלומר בזכות תצורה ולא בזכות הקוד.
        # ⚠️ החוב עצמו לא נסגר: תאריך סיום העסקה הוא עובדת HR שלרוב מתרחשת
        # בעבר, ואין לגזור אותה משעון ברגע שאדמין לוחץ. הפתרון הוא קלט מפורש -
        # רשום ב-HANDOFF.md. השינוי כאן רק מסיר את התלות במארח.
        emp.termination_date = business_today()
        append_event(db, event_type="EMPLOYEE_STATUS_CHANGED", aggregate_type="Employee",
                    aggregate_id=employee_id,
                    payload={"status": "TERMINATED", "termination_date": emp.termination_date},
                    effective_date=emp.termination_date, actor_user_id=current_user.user_id)
        record_audit_event(db, "Employee", employee_id, "SOFT_DELETE_TERMINATE", current_user.user_id,
                            before={"status": before_status}, after={"status": "TERMINATED", "termination_date": emp.termination_date})
        db.commit()
        return {"employee_id": employee_id, "deleted": "soft", "new_status": "TERMINATED"}


# ===================================================================
# COMPANY ADMIN PORTAL - Company profile
# ===================================================================

@router.get("/admin/company", response_model=CompanyOut)
def get_my_company(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    comp = db.query(Company).filter(Company.company_id == current_user.company_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")
    return comp


@router.put("/admin/company", response_model=CompanyOut)
def update_my_company(payload: CompanyUpdateRequest, current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    comp = db.query(Company).filter(Company.company_id == current_user.company_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")
    before = {"name": comp.name, "country_code": comp.country_code}
    if payload.name is not None:
        comp.name = payload.name
    if payload.country_code is not None:
        comp.country_code = payload.country_code
    record_audit_event(db, "Company", comp.company_id, "UPDATE", current_user.user_id,
                        before=before, after={"name": comp.name, "country_code": comp.country_code})
    db.commit()
    db.refresh(comp)
    return comp


@router.delete("/admin/company")
def delete_my_company(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    comp = db.query(Company).filter(Company.company_id == current_user.company_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")

    emp_count = db.query(Employee).filter(Employee.company_id == comp.company_id).count()
    pool_count = db.query(OptionPool).filter(OptionPool.company_id == comp.company_id).count()
    trustee_count = db.query(Trustee).filter(Trustee.company_id == comp.company_id).count()

    if emp_count == 0 and pool_count == 0 and trustee_count == 0:
        orphan_user_ids = [u.user_id for u in db.query(User.user_id).filter(User.company_id == comp.company_id).all()]
        if orphan_user_ids:
            db.query(UserSession).filter(UserSession.user_id.in_(orphan_user_ids)).delete(synchronize_session=False)
            db.query(User).filter(User.company_id == comp.company_id).delete()
        record_audit_event(db, "Company", comp.company_id, "HARD_DELETE", current_user.user_id, before={"name": comp.name})
        db.delete(comp)
        db.commit()
        return {"company_id": comp.company_id, "deleted": "hard"}
    else:
        record_audit_event(db, "Company", comp.company_id, "SOFT_DELETE_DEACTIVATE", current_user.user_id,
                            before={"is_active": comp.is_active}, after={"is_active": False})
        comp.is_active = False
        db.commit()
        return {"company_id": comp.company_id, "deleted": "soft", "is_active": False}


@router.get("/admin/pools", response_model=List[PoolOut])
def list_pools(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    return db.query(OptionPool).filter(OptionPool.company_id == current_user.company_id).all()


@router.get("/admin/grants", response_model=List[GrantOut])
def list_company_grants(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    pool_ids = [p.pool_id for p in db.query(OptionPool.pool_id).filter(OptionPool.company_id == current_user.company_id).all()]
    return db.query(Grant).filter(Grant.pool_id.in_(pool_ids)).all()


@router.get("/admin/exercise-requests", response_model=List[ExerciseRequestOut])
def list_pending_requests_admin(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    pool_ids = [p.pool_id for p in db.query(OptionPool.pool_id).filter(OptionPool.company_id == current_user.company_id).all()]
    grant_ids = [g.grant_id for g in db.query(Grant.grant_id).filter(Grant.pool_id.in_(pool_ids)).all()]
    return db.query(ExerciseRequest).filter(ExerciseRequest.grant_id.in_(grant_ids)).all()


@router.patch("/admin/exercise-requests/{request_id}", response_model=ExerciseRequestOut)
def review_exercise_request_admin(request_id: str, payload: ExerciseRequestReview,
                                   current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    req = db.query(ExerciseRequest).filter(ExerciseRequest.request_id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    grant = db.query(Grant).filter(Grant.grant_id == req.grant_id).first()
    pool = db.query(OptionPool).filter(OptionPool.pool_id == grant.pool_id).first()
    if pool.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Not your company's grant")

    # דחייה תמיד מותרת; רק אישור צריך לעמוד בשלוש הבדיקות.
    if payload.approve:
        _assert_request_approvable(db, req, grant)

    _decide_exercise_request(db, req, payload, current_user.user_id)
    db.commit()
    db.refresh(req)
    return req


@router.get("/admin/audit-log", response_model=List[AuditLogOut])
def get_audit_log(entity_type: str, entity_id: str,
                   current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    """היסטוריית שינויים לישות בודדת. בודק בעלות לפי company_id של current_user, לפי סוג הישות."""
    if entity_type == "Employee":
        emp = db.query(Employee).filter(Employee.employee_id == entity_id).first()
        if not emp or emp.company_id != current_user.company_id:
            raise HTTPException(status_code=403, detail="Not your company's employee")
    elif entity_type in ("Grant", "TaxSimulation"):
        # TaxSimulation נרשם עם entity_id=grant_id (ראו simulate_exercise) - אותה בדיקת בעלות כמו Grant.
        grant = db.query(Grant).filter(Grant.grant_id == entity_id).first()
        pool = db.query(OptionPool).filter(OptionPool.pool_id == grant.pool_id).first() if grant else None
        if not grant or not pool or pool.company_id != current_user.company_id:
            raise HTTPException(status_code=403, detail="Not your company's grant")
    elif entity_type == "Company":
        if entity_id != current_user.company_id:
            raise HTTPException(status_code=403, detail="Not your company")
    elif entity_type == "ExerciseRequest":
        req = db.query(ExerciseRequest).filter(ExerciseRequest.request_id == entity_id).first()
        grant = db.query(Grant).filter(Grant.grant_id == req.grant_id).first() if req else None
        pool = db.query(OptionPool).filter(OptionPool.pool_id == grant.pool_id).first() if grant else None
        if not req or not pool or pool.company_id != current_user.company_id:
            raise HTTPException(status_code=403, detail="Not your company's exercise request")
    else:
        raise HTTPException(status_code=400, detail="Unsupported entity_type")

    return (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.occurred_at.desc())
        .all()
    )


# ===================================================================
# LEDGER (v0.6.0 שלב 3) - ציר זמן ושאילתה בי-טמפורלית. admin-only (דרך א' -
# הבוס הקיים מקבל גישה, לא נוצר תפקיד "מבקר" חדש - ראו GOAL.md/FEATURE_SPEC.md).
# ===================================================================

def _assert_ledger_ownership(db: Session, aggregate_type: str, aggregate_id: str, current_user: User) -> None:
    """מאשר גישה מול ledger_ownership - אינדקס נפרד ולא-חוזר, לעולם לא מול
    דאטה משוחזר/מוקרן (project()). זו בדיוק ההגנה מפני IDOR שחוזר בצורה חדשה
    במסכי v0.6.0, שהוזכרה בסקירת האבטחה בתכנון: מסך חדש שמאשר גישה מול
    הפרויקציה עצמה היה חוזר על אותו דפוס שכבר תוקן פעמיים (list_employees,
    employee/dashboard/{id}).

    בודק גם ש-aggregate_type בכתובת תואם לסוג האמיתי שנשמר - אחרת מזהה תקין
    של ישות אחת (למשל מענק) עם aggregate_type של ישות אחרת (למשל עובד) היה
    עובר את בדיקת ה-company_id ומופעל מול הפרויקטור הלא נכון."""
    ownership = db.get(LedgerOwnership, aggregate_id)
    if not ownership or ownership.company_id != current_user.company_id or ownership.aggregate_type != aggregate_type:
        raise HTTPException(status_code=403, detail="Not your company's data")


@router.get("/admin/ledger/{aggregate_type}/{aggregate_id}/events", response_model=List[LedgerEventOut])
def get_ledger_timeline(aggregate_type: str, aggregate_id: str,
                         current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                         db: Session = Depends(get_db)):
    """ציר הזמן המלא של ישות אחת - "מה קרה ומתי", בסדר הקיפול הקנוני."""
    if aggregate_type not in LEDGER_AGGREGATE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported aggregate_type: {aggregate_type}")
    _assert_ledger_ownership(db, aggregate_type, aggregate_id, current_user)

    events = events_for(db, aggregate_id)
    return [
        LedgerEventOut(event_id=e.event_id, event_type=e.event_type,
                      effective_date=e.effective_date, recorded_at=e.recorded_at,
                      source=e.source, payload=json.loads(e.payload),
                      corrects_event_id=e.corrects_event_id)
        for e in events
    ]


@router.get("/admin/ledger/{aggregate_type}/{aggregate_id}/as-of", response_model=LedgerProjectionOut)
def get_ledger_as_of(aggregate_type: str, aggregate_id: str,
                     effective_date: Optional[date] = None, knowledge_date: Optional[datetime] = None,
                     current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                     db: Session = Depends(get_db)):
    """שאילתה בי-טמפורלית: 'מה חשבנו נכון' לפי כל אחד משני צירי הזמן בנפרד.
    שני הפרמטרים None => כל ההיסטוריה, כלומר "מה נכון עכשיו". state=None
    כשאין אירועים עד לחתך המבוקש - "אין נתון", לא 0/ריק."""
    if aggregate_type not in LEDGER_AGGREGATE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported aggregate_type: {aggregate_type}")
    _assert_ledger_ownership(db, aggregate_type, aggregate_id, current_user)

    # נקודת הכשל האמיתית של החתך הבי-טמפורלי: FastAPI מפרסר
    # ?knowledge_date=...+03:00 ל-datetime aware, וההשוואה מול recorded_at
    # ה-naive הייתה מוחקת את ההיסט בשקט - כלומר מחזירה אירוע שנוצר *אחרי*
    # נקודת החתך כאילו המערכת כבר ידעה עליו. נרמול כאן, לפני כל שאילתה.
    knowledge_date = ensure_utc(knowledge_date)

    state = project(db, aggregate_type, aggregate_id,
                    as_of_effective_date=effective_date, as_of_knowledge_date=knowledge_date)
    return LedgerProjectionOut(aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                               as_of_effective_date=effective_date, as_of_knowledge_date=knowledge_date,
                               state=state)


# --- COMPANY ADMIN PORTAL ENDPOINTS (legacy) ---
@router.patch("/admin/employees/{employee_id}/status")
def update_employee_status(employee_id: str, payload: EmployeeStatusUpdate,
                            current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    employee = db.query(Employee).filter(Employee.employee_id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    if employee.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot modify an employee outside your company")

    before_status = employee.status
    returned_options = 0.0

    if payload.status == EmployeeStatus.TERMINATED:
        # ההחזרה לפול מותרת רק במעבר *אמיתי* למצב עזיבה. קודם אותה קריאה פעמיים
        # (או מעבר TERMINATED -> TERMINATED עם תאריך אחר) הזרימה את האופציות שלא
        # הבשילו לפול שוב ושוב, וכל הרצה כזו הזיזה את יתרות הפול עוד צעד מהמענקים.
        already_terminated = before_status in (EmployeeStatus.TERMINATED, EmployeeStatus.DECEASED)

        if payload.return_unvested_to_pool and not already_terminated:
            for grant in employee.grants:
                try:
                    vested = DeterministicESOPEngine.calculate_vested_options(
                        grant, grant.vesting_schedule, payload.effective_date)
                except MissingVestingScheduleError:
                    # חוסם את כל הפעולה: בלי לוח הבשלה אין דרך לדעת מה הבשיל, וניחוש
                    # כאן מזיז כספית גם את הפול וגם את זכויות העובד.
                    raise HTTPException(
                        status_code=409,
                        detail=(f"Grant {grant.grant_id} has no vesting schedule - cannot determine "
                                "how many options to return to the pool. Attach a schedule first."),
                    )
                unvested = grant.total_options - vested
                returned_options += unvested

                pool = db.query(OptionPool).filter(OptionPool.pool_id == grant.pool_id).first()
                if pool:
                    # allocated_shares מייצג את מה שמוקצה *בפועל* (outstanding), ולא את
                    # סך מה שהוענק היסטורית - לכן הוא קטן מסך total_options של המענקים
                    # אחרי עזיבה, וזה נכון ולא דריפט.
                    pool.unallocated_shares += unvested
                    pool.allocated_shares -= unvested
                    append_event(db, event_type="POOL_UNVEST_RETURNED", aggregate_type="OptionPool",
                                aggregate_id=pool.pool_id,
                                payload={"amount": unvested, "grant_id": grant.grant_id,
                                        "reason": "employee_terminated"},
                                effective_date=payload.effective_date, actor_user_id=current_user.user_id)

        employee.termination_date = payload.effective_date

    employee.status = payload.status

    # v0.6.0: אירוע לכל שינוי סטטוס, לא רק עזיבה - הקוד עצמו מבצע השמה גנרית.
    append_event(db, event_type="EMPLOYEE_STATUS_CHANGED", aggregate_type="Employee",
                aggregate_id=employee_id,
                payload={"status": employee.status.value if hasattr(employee.status, "value") else employee.status,
                        "termination_date": employee.termination_date},
                effective_date=payload.effective_date, actor_user_id=current_user.user_id)

    record_audit_event(db, "Employee", employee_id, "STATUS_CHANGE", current_user.user_id,
                        before={"status": before_status.value if hasattr(before_status, "value") else before_status},
                        after={"status": employee.status, "effective_date": payload.effective_date,
                               "returned_options": returned_options})
    db.commit()
    return {"employee_id": employee_id, "status": employee.status, "returned_options": returned_options}


@router.post("/admin/grants", response_model=CreateGrantResponse)
def create_grant(payload: CreateGrantRequest,
                  current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    """יצירת מענק אופציות חדש לעובד, כולל שיוך לפול, יצירת לוח הבשלה, ועדכון יתרות הפול."""
    employee = db.query(Employee).filter(Employee.employee_id == payload.employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    # בדיקת גיל במועד ההענקה. חסרה לגמרי קודם - אפשר היה להעניק אופציות לקטין.
    # birth_date חסר נחסם גם הוא: "לא בדקנו" אינו "עבר את הבדיקה".
    if employee.birth_date is None:
        raise HTTPException(
            status_code=400,
            detail=("Employee birth_date is required to validate grant eligibility "
                    f"(minimum age {MINIMUM_GRANT_AGE_YEARS})"),
        )
    eligible_from = shift_months(employee.birth_date, MINIMUM_GRANT_AGE_YEARS * 12)
    if payload.grant_date < eligible_from:
        raise HTTPException(
            status_code=400,
            detail=(f"Employee is under {MINIMUM_GRANT_AGE_YEARS} on the grant date "
                    f"(eligible from {eligible_from})"),
        )

    pool = db.query(OptionPool).filter(OptionPool.pool_id == payload.pool_id).first()
    if not pool:
        raise HTTPException(status_code=404, detail="Option pool not found")
    if pool.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot grant against a pool outside your company")

    if payload.trustee_id:
        trustee = db.query(Trustee).filter(Trustee.trustee_id == payload.trustee_id).first()
        if not trustee:
            raise HTTPException(status_code=404, detail="Trustee not found")

    if payload.total_options <= 0:
        raise HTTPException(status_code=400, detail="total_options must be positive")

    if payload.total_options > pool.unallocated_shares:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough unallocated shares in pool (available: {pool.unallocated_shares})",
        )

    grant = Grant(
        employee_id=payload.employee_id,
        pool_id=payload.pool_id,
        trustee_id=payload.trustee_id,
        grant_date=payload.grant_date,
        grant_type=payload.grant_type,
        total_options=payload.total_options,
        exercise_price=payload.exercise_price,
        currency=payload.currency,
        post_termination_window_days=payload.post_termination_window_days,
    )
    db.add(grant)

    pool.allocated_shares += payload.total_options
    pool.unallocated_shares -= payload.total_options

    db.flush()  # grant.grant_id זמין מכאן

    schedule = VestingSchedule(
        grant_id=grant.grant_id,
        start_date=payload.grant_date,
        cliff_months=payload.cliff_months,
        total_months=payload.total_months,
    )
    db.add(schedule)
    db.flush()  # schedule.schedule_id זמין מכאן

    # v0.6.0: שלושת אירועי הבסיס (ESTABLISHED/CREATED) לישויות שנוצרו הרגע -
    # אותם סוגי אירוע בדיוק כמו backfill_ledger.py, כי זו אותה עובדה בדיוק,
    # רק שמקורה חי (source=LIVE, ברירת המחדל של append_event) ולא גיבוי.
    # record_ownership על הפול הוא הגנתי-בלבד: אין endpoint שיוצר פול, אז
    # הבעלות שלו כבר אמורה להיקבע ע"י backfill - קריאה שנייה כאן לא עושה כלום
    # אם היא כבר קיימת (ראו record_ownership).
    record_ownership(db, aggregate_id=pool.pool_id, aggregate_type="OptionPool",
                     company_id=pool.company_id)
    record_ownership(db, aggregate_id=grant.grant_id, aggregate_type="Grant",
                     company_id=pool.company_id, trustee_id=grant.trustee_id,
                     employee_id=grant.employee_id)
    record_ownership(db, aggregate_id=schedule.schedule_id, aggregate_type="VestingSchedule",
                     company_id=pool.company_id, trustee_id=grant.trustee_id,
                     employee_id=grant.employee_id)

    append_event(db, event_type="GRANT_CREATED", aggregate_type="Grant", aggregate_id=grant.grant_id,
                payload={"employee_id": grant.employee_id, "pool_id": grant.pool_id,
                        "trustee_id": grant.trustee_id,
                        "grant_type": grant.grant_type.value if hasattr(grant.grant_type, "value") else grant.grant_type,
                        "total_options": grant.total_options, "exercise_price": grant.exercise_price,
                        "currency": grant.currency,
                        "post_termination_window_days": grant.post_termination_window_days,
                        "trustee_deposit_date": None},
                effective_date=grant.grant_date, actor_user_id=current_user.user_id)
    append_event(db, event_type="POOL_ALLOCATED", aggregate_type="OptionPool", aggregate_id=pool.pool_id,
                payload={"amount": payload.total_options, "grant_id": grant.grant_id},
                effective_date=grant.grant_date, actor_user_id=current_user.user_id)
    append_event(db, event_type="VESTING_SCHEDULE_ESTABLISHED", aggregate_type="VestingSchedule",
                aggregate_id=schedule.schedule_id,
                payload={"start_date": schedule.start_date, "cliff_months": schedule.cliff_months,
                        "total_months": schedule.total_months, "paused_days_total": schedule.paused_days_total},
                effective_date=schedule.start_date, actor_user_id=current_user.user_id)

    record_audit_event(db, "Grant", grant.grant_id, "CREATE", current_user.user_id,
                        after={"employee_id": grant.employee_id, "pool_id": grant.pool_id,
                               "total_options": grant.total_options, "grant_type": grant.grant_type.value,
                               "exercise_price": grant.exercise_price})

    db.commit()
    db.refresh(grant)
    db.refresh(schedule)
    db.refresh(pool)

    return CreateGrantResponse(
        grant_id=grant.grant_id,
        employee_id=grant.employee_id,
        pool_id=grant.pool_id,
        total_options=grant.total_options,
        vesting_schedule_id=schedule.schedule_id,
        pool_allocated_shares=pool.allocated_shares,
        pool_unallocated_shares=pool.unallocated_shares,
    )


@router.post("/admin/grants/{grant_id}/vesting-pause", response_model=VestingPauseResponse)
def record_vesting_pause(grant_id: str, payload: VestingPauseRequest,
                         current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                         db: Session = Depends(get_db)):
    """v0.6.0 שלב 4: רושם תקופת חופשה ללא תשלום *שהסתיימה* על מענק קיים -
    לא "התחל הקפאה"/"סיים הקפאה" כשני אירועים נפרדים. שום מקום אחר במערכת
    לא עוקב אחרי "הקפאה פתוחה שטרם נסגרה" (בשונה מ-termination_date, שהוא
    שדה עצמאי אמיתי) - אדמין רושם את התקופה אחרי שהיא כבר ידועה במלואה,
    בדיוק כמו trustee_deposit_date. סוגר את הפער ב-VestingSchedule.paused_days_total
    שלא הייתה לו שום דרך להיכתב לפני הגרסה הזו."""
    grant = db.query(Grant).filter(Grant.grant_id == grant_id).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")
    pool = db.query(OptionPool).filter(OptionPool.pool_id == grant.pool_id).first()
    if not pool or pool.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot modify a grant outside your company")

    schedule = grant.vesting_schedule
    if not schedule:
        raise HTTPException(
            status_code=409,
            detail=f"Grant {grant_id} has no vesting schedule - attach one before recording a pause",
        )

    if payload.end_date <= payload.start_date:
        raise HTTPException(status_code=400, detail="end_date must be after start_date")

    # מניעת חפיפה עם תקופת הקפאה שכבר נרשמה - אותו דפוס בדיוק כמו מניעת אישור
    # כפול על בקשת מימוש (v0.5.0): לבדוק לפני שכותבים, לא לסמוך על כך שאף אחד
    # לא ירשום פעמיים. חפיפת טווחים סטנדרטית: start_A < end_B וגם start_B < end_A.
    for existing in events_for(db, schedule.schedule_id):
        if existing.event_type != "VESTING_PAUSE_RECORDED":
            continue
        p = json.loads(existing.payload)
        existing_start = date.fromisoformat(p["start_date"])
        existing_end = date.fromisoformat(p["end_date"])
        if payload.start_date < existing_end and existing_start < payload.end_date:
            raise HTTPException(
                status_code=400,
                detail=f"Overlaps an existing pause period ({existing_start} to {existing_end})",
            )

    days = (payload.end_date - payload.start_date).days
    before_total = schedule.paused_days_total
    schedule.paused_days_total += days

    # הגנתי, כמו ב-create_grant: לוח הבשלה שאין לו רשומת בעלות (למשל, נוצר
    # לפני v0.6.0 ולא עבר גיבוי) מקבל אחת עכשיו, ולא נשאר תקוע ב-403 בכל
    # שאילתת ציר-זמן/as-of עתידית עליו.
    record_ownership(db, aggregate_id=schedule.schedule_id, aggregate_type="VestingSchedule",
                     company_id=pool.company_id, trustee_id=grant.trustee_id,
                     employee_id=grant.employee_id)
    append_event(db, event_type="VESTING_PAUSE_RECORDED", aggregate_type="VestingSchedule",
                aggregate_id=schedule.schedule_id,
                payload={"start_date": payload.start_date, "end_date": payload.end_date, "days": days},
                effective_date=payload.end_date, actor_user_id=current_user.user_id)

    record_audit_event(db, "VestingSchedule", schedule.schedule_id, "PAUSE_RECORDED", current_user.user_id,
                        before={"paused_days_total": before_total},
                        after={"paused_days_total": schedule.paused_days_total, "days_added": days})

    db.commit()
    db.refresh(schedule)
    return VestingPauseResponse(schedule_id=schedule.schedule_id, days_added=days,
                                paused_days_total=schedule.paused_days_total)


# ===================================================================
# TRUSTEE PORTAL
# ===================================================================

@router.get("/trustee/portfolio", response_model=List[TrusteePortfolioItem])
def trustee_portfolio(current_user: User = Depends(require_roles(UserRole.TRUSTEE)), db: Session = Depends(get_db)):
    grants = db.query(Grant).filter(Grant.trustee_id == current_user.trustee_id).all()
    today = business_today()
    result = []
    for g in grants:
        emp = g.employee
        try:
            vested = _vested_at(g, today)
            vesting_data_missing = False
        except MissingVestingScheduleError:
            vested, vesting_data_missing = None, True
        is_met, end_date = DeterministicESOPEngine.check_trustee_holding_period(g, today)
        result.append(TrusteePortfolioItem(
            grant_id=g.grant_id,
            employee_id=emp.employee_id if emp else None,
            employee_name=f"{emp.first_name} {emp.last_name}" if emp else None,
            company_id=emp.company_id if emp else None,
            company_name=emp.company.name if (emp and emp.company) else None,
            total_options=g.total_options,
            vested_options=vested,
            vesting_data_missing=vesting_data_missing,
            trustee_deposit_date=g.trustee_deposit_date,
            holding_period_end_date=end_date,
            is_trustee_holding_period_met=is_met,
        ))
    return result


@router.get("/trustee/exercise-requests", response_model=List[ExerciseRequestOut])
def list_pending_requests_trustee(current_user: User = Depends(require_roles(UserRole.TRUSTEE)), db: Session = Depends(get_db)):
    grant_ids = [g.grant_id for g in db.query(Grant.grant_id).filter(Grant.trustee_id == current_user.trustee_id).all()]
    return db.query(ExerciseRequest).filter(ExerciseRequest.grant_id.in_(grant_ids)).all()


@router.patch("/trustee/exercise-requests/{request_id}", response_model=ExerciseRequestOut)
def review_exercise_request_trustee(request_id: str, payload: ExerciseRequestReview,
                                     current_user: User = Depends(require_roles(UserRole.TRUSTEE)), db: Session = Depends(get_db)):
    req = db.query(ExerciseRequest).filter(ExerciseRequest.request_id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    grant = db.query(Grant).filter(Grant.grant_id == req.grant_id).first()
    if grant.trustee_id != current_user.trustee_id:
        raise HTTPException(status_code=403, detail="Not your trusteeship")

    # אותן בדיקות בדיוק כמו בנתיב ה-admin, ואותה נקודת כתיבה (_decide_exercise_request) -
    # קודם הנאמן היה נתיב האישור הפרוץ יותר כי שני הנתיבים כפלו את הלוגיקה בנפרד.
    if payload.approve:
        _assert_request_approvable(db, req, grant)

    _decide_exercise_request(db, req, payload, current_user.user_id)
    db.commit()
    db.refresh(req)
    return req


@router.patch("/trustee/confirm-deposit/{grant_id}")
def confirm_trustee_deposit(grant_id: str, deposit_date: date,
                             current_user: User = Depends(require_roles(UserRole.TRUSTEE)), db: Session = Depends(get_db)):
    grant = db.query(Grant).filter(Grant.grant_id == grant_id).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")
    if grant.trustee_id != current_user.trustee_id:
        raise HTTPException(status_code=403, detail="This grant is not under your trusteeship")

    # תיקון backdating (v0.6.0, מאושר במפורש): לא ניתן להפקיד מניות אצל הנאמן
    # לפני שהמענק בכלל נוצר - נבדק מול הדאטה הקיים לפני התכנון (0 הפרות).
    # קודם לא הייתה שום בדיקה כאן, וזה בדיוק מה שמאפשר לזכות במסלול רווח הון
    # מוקדם מדי אם ההפקדה "נרשמת" לתאריך שקדם למענק עצמו.
    if deposit_date < grant.grant_date:
        raise HTTPException(
            status_code=400,
            detail=f"Deposit date {deposit_date} cannot precede the grant date {grant.grant_date}",
        )

    before_deposit = grant.trustee_deposit_date
    grant.trustee_deposit_date = deposit_date
    record_audit_event(db, "Grant", grant_id, "DEPOSIT_CONFIRMED", current_user.user_id,
                        before={"trustee_deposit_date": before_deposit}, after={"trustee_deposit_date": deposit_date})
    append_event(db, event_type="TRUSTEE_DEPOSIT_CONFIRMED", aggregate_type="Grant",
                aggregate_id=grant_id, payload={"deposit_date": deposit_date},
                effective_date=deposit_date, actor_user_id=current_user.user_id)
    db.commit()

    return {"grant_id": grant_id, "deposit_date": str(deposit_date), "status": "DEPOSIT_CONFIRMED"}


# ===================================================================
# EMPLOYEE PORTAL
# ===================================================================

@router.get("/employee/dashboard/{employee_id}")
def get_employee_dashboard(employee_id: str, current_user: User = Depends(require_roles(UserRole.EMPLOYEE)), db: Session = Depends(get_db)):
    # עובד רואה רק את עצמו. קודם כל employee_id היה נגיש לכל עובד מאומת (IDOR).
    if employee_id != current_user.employee_id:
        raise HTTPException(status_code=403, detail="You can only view your own dashboard")

    employee = db.query(Employee).filter(Employee.employee_id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    grants_data = []
    today = business_today()

    for grant in employee.grants:
        is_trustee_met, end_date = DeterministicESOPEngine.check_trustee_holding_period(grant, today)
        is_within_ptw, ptw_deadline = DeterministicESOPEngine.check_post_termination_exercise_window(
            grant, employee, today
        )

        # מענק בלי לוח הבשלה מסומן במפורש כנתון חסר, ולא מוצג כ-vested=0. עובד
        # שרואה 0 לא יכול להבחין בין "עוד לא הבשיל" לבין "חסר לנו הנתון".
        try:
            vested = _vested_at(grant, today)
            vesting_data_missing = False
        except MissingVestingScheduleError:
            vested, vesting_data_missing = None, True

        grants_data.append({
            "grant_id": grant.grant_id,
            "total_options": grant.total_options,
            "vested_options": vested,
            "vesting_data_missing": vesting_data_missing,
            "exercise_price": grant.exercise_price,
            "is_trustee_holding_period_met": is_trustee_met,
            "holding_period_end_date": str(end_date),
            "is_within_post_termination_window": is_within_ptw,
            "post_termination_exercise_deadline": str(ptw_deadline) if ptw_deadline else None,
        })

    return {"employee_name": f"{employee.first_name} {employee.last_name}", "grants": grants_data}


@router.post("/employee/simulate-exercise", response_model=ExerciseSimulationResponse)
def simulate_exercise(payload: ExerciseSimulationRequest, current_user: User = Depends(require_roles(UserRole.EMPLOYEE)), db: Session = Depends(get_db)):
    grant = db.query(Grant).filter(Grant.grant_id == payload.grant_id).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")
    # בעלות. הבדיקה הזו לא הופיעה במפת הבאגים אבל חסרה כאן בפועל: כל עובד מאומת
    # יכול היה להריץ סימולציית מס על מענק של עובד אחר ולראות ממנה את מחיר המימוש,
    # את השווי ואת סכום המס שלו.
    if grant.employee_id != current_user.employee_id:
        raise HTTPException(status_code=403, detail="This grant does not belong to you")

    is_met, end_date = DeterministicESOPEngine.check_trustee_holding_period(grant, payload.exercise_date)
    is_within_ptw, ptw_deadline = DeterministicESOPEngine.check_post_termination_exercise_window(
        grant, grant.employee, payload.exercise_date
    )

    latest_price = db.query(StockPricesHistory).filter(StockPricesHistory.company_id == grant.employee.company_id).order_by(StockPricesHistory.price_date.desc()).first()
    stock_price = latest_price.fmv_price if latest_price else grant.exercise_price

    total_cost = payload.options_to_exercise * grant.exercise_price
    gain = max(0.0, (stock_price - grant.exercise_price) * payload.options_to_exercise)

    grant_type_value = grant.grant_type.value if hasattr(grant.grant_type, "value") else grant.grant_type
    try:
        tax_result = TaxCalculationEngine.calculate_tax(
            db, grant.employee.country_code, grant_type_value, payload.exercise_date, gain,
        )
    except MissingTaxRuleError as e:
        # 409 ולא 500: זה לא קלט שגוי מהעובד, אלא נתון מס חסר שלא ניתן לגשר
        # עליו בשקט - בדיוק אותו עיקרון כמו MissingVestingScheduleError.
        # ה-reason (NEVER_MODELED / NO_RULE_EFFECTIVE_AS_OF_DATE /
        # PACK_HAS_NO_DETAIL_ROWS) נשמר ב-audit לצורך triage, לא נחשף כקוד HTTP
        # נפרד ללקוח - שני המצבים דורשים מהעובד את אותה פעולה (לפנות למנהל).
        record_audit_event(
            db, "TaxSimulation", grant.grant_id, "SIMULATE_FAILED", current_user.user_id,
            after={"exercise_date": payload.exercise_date, "options_to_exercise": payload.options_to_exercise,
                  "gain": gain, "reason": e.reason},
        )
        db.commit()
        raise HTTPException(
            status_code=409,
            detail=(f"No tax rule found for {grant.employee.country_code}/{grant_type_value} "
                    f"as of {payload.exercise_date} - contact your plan administrator."),
        )

    # רישום audit לכל סימולציה - כדי שאפשר יהיה לבדוק בדיעבד בדיוק לפי איזו
    # גרסת טבלת מס/מדרגות חושב סכום נתון (נדרש עבור תרחישי ביקורת עתידיים).
    record_audit_event(
        db, "TaxSimulation", grant.grant_id, "SIMULATE", current_user.user_id,
        after={
            "exercise_date": payload.exercise_date, "options_to_exercise": payload.options_to_exercise,
            "gain": gain, "tax_method": tax_result.method, "tax_table_effective_date": tax_result.table_effective_date,
            "effective_rate": tax_result.effective_rate, "tax_amount": tax_result.tax_amount,
            "source": tax_result.source_url, "tax_rule_pack_id": tax_result.pack_id,
        },
    )
    db.commit()

    return ExerciseSimulationResponse(
        grant_id=grant.grant_id,
        is_trustee_holding_period_met=is_met,
        holding_period_end_date=end_date,
        current_stock_price=stock_price,
        total_exercise_cost=total_cost,
        estimated_tax_amount=tax_result.tax_amount,
        applied_tax_rate=tax_result.effective_rate,
        tax_rule_source=tax_result.source_url,
        tax_calculation_method=tax_result.method,
        tax_table_effective_date=tax_result.table_effective_date,
        tax_rule_pack_id=tax_result.pack_id,
        is_within_post_termination_window=is_within_ptw,
        post_termination_exercise_deadline=ptw_deadline,
    )


@router.post("/employee/exercise-requests", response_model=ExerciseRequestOut)
def create_exercise_request(payload: ExerciseRequestCreate, current_user: User = Depends(require_roles(UserRole.EMPLOYEE)), db: Session = Depends(get_db)):
    grant = db.query(Grant).filter(Grant.grant_id == payload.grant_id).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")
    if grant.employee_id != current_user.employee_id:
        raise HTTPException(status_code=403, detail="This grant does not belong to you")

    is_within_ptw, ptw_deadline = DeterministicESOPEngine.check_post_termination_exercise_window(
        grant, grant.employee, business_today()
    )
    if not is_within_ptw:
        raise HTTPException(
            status_code=400,
            detail=f"Post-termination exercise window has closed (deadline was {ptw_deadline})",
        )

    if payload.options_to_exercise <= 0:
        raise HTTPException(status_code=400, detail="options_to_exercise must be positive")

    # חסימה כבר בהגשה, ולא רק באישור: שתי בקשות חופפות שיושבות PENDING יחד מציגות
    # לעובד תמונה שקרית (הוא "ביקש" יותר ממה שיש לו) ומעמיסות על המאשר את התפקיד
    # שהמערכת אמורה למלא.
    vested = _vested_or_conflict(grant, business_today())
    committed = _options_committed(
        db, grant.grant_id,
        (ExerciseRequestStatus.PENDING, ExerciseRequestStatus.APPROVED))
    if payload.options_to_exercise + committed > vested:
        raise HTTPException(
            status_code=400,
            detail=(f"Requested {payload.options_to_exercise:.0f} options but only "
                    f"{max(0.0, vested - committed):.0f} are available "
                    f"({vested:.0f} vested, {committed:.0f} already requested or approved)"),
        )

    req = ExerciseRequest(
        grant_id=grant.grant_id,
        employee_id=current_user.employee_id,
        options_requested=payload.options_to_exercise,
        status=ExerciseRequestStatus.PENDING,
    )
    db.add(req)
    db.flush()

    # v0.6.0: אירוע בסיס - בלי זה, EXERCISE_REQUEST_DECIDED עתידי (אישור/דחייה)
    # ייפול על state=None בלי אפקט (ראו project_exercise_request).
    record_ownership(db, aggregate_id=req.request_id, aggregate_type="ExerciseRequest",
                     company_id=_company_id_of_grant(db, grant), employee_id=current_user.employee_id)
    append_event(db, event_type="EXERCISE_REQUEST_SUBMITTED", aggregate_type="ExerciseRequest",
                aggregate_id=req.request_id,
                payload={"options_requested": req.options_requested, "grant_id": grant.grant_id},
                effective_date=business_today(), actor_user_id=current_user.user_id)

    db.commit()
    db.refresh(req)
    return req


@router.get("/employee/exercise-requests", response_model=List[ExerciseRequestOut])
def list_my_exercise_requests(current_user: User = Depends(require_roles(UserRole.EMPLOYEE)), db: Session = Depends(get_db)):
    return db.query(ExerciseRequest).filter(ExerciseRequest.employee_id == current_user.employee_id).all()


# ===================================================================
# מסמכים ואישור קבלה פנימי (v0.9.0 שלבים 1-2)
# *** לא חתימה - ראו הערת models.py.Document ***
#
# כל endpoint שמחזיר/מוריד/משנה מסמך קורא ל-assert_document_access **ראשון**.
# זו הנקודה שכבר נכשלה 3 פעמים בעבר (QA_TESTBOOK.md P2) - פונקציה אחת, לא
# בדיקה מועתקת. שאילתות *רשימה* מסוננות ב-SQL לפי היקף מ-current_user בלבד,
# בלי שום פרמטר שהלקוח יכול לדרוס.
# ===================================================================

def _documents_out(db: Session, documents: List[Document]) -> List[DocumentOut]:
    """הרכבת תצוגת המסמך: שם העובד ותאריך המענק יושבים בטבלאות אחרות, ובלעדיהם
    השורה בפורטל היא UUID בלבד. שאילתה אחת לכל טבלה ולא אחת לכל מסמך - ספריית
    המסמכים של חברה גדולה לא תייצר N+1."""
    if not documents:
        return []
    employees = {
        e.employee_id: e for e in db.query(Employee)
        .filter(Employee.employee_id.in_({d.employee_id for d in documents})).all()
    }
    grants = {
        g.grant_id: g for g in db.query(Grant)
        .filter(Grant.grant_id.in_({d.grant_id for d in documents})).all()
    }
    out = []
    for d in documents:
        employee = employees.get(d.employee_id)
        grant = grants.get(d.grant_id)
        out.append(DocumentOut(
            document_id=d.document_id, template_type=d.template_type, grant_id=d.grant_id,
            status=d.status.value, version=d.version, is_latest=d.is_latest,
            file_sha256=d.file_sha256, generated_at=d.generated_at,
            sent_at=d.sent_at, acknowledged_at=d.acknowledged_at,
            # None ולא "" - ראו ההערה ב-DocumentOut. שורה יתומה היא באג נתונים
            # שה-UI צריך להראות ככזה, לא שם ריק שנראה תקין.
            employee_name=f"{employee.first_name} {employee.last_name}" if employee else None,
            grant_date=grant.grant_date if grant else None,
        ))
    return out


def _document_out(db: Session, document: Document) -> DocumentOut:
    return _documents_out(db, [document])[0]


def _load_document_or_404(db: Session, document_id: str, current_user: User) -> Document:
    document = db.query(Document).filter(Document.document_id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    assert_document_access(document, current_user)
    return document


def _transition_document(db: Session, document: Document, target: DocumentStatus,
                         current_user: User, action: str) -> Document:
    """מעבר מצב יחיד + audit, בטרנזקציה אחת. הוולידציה קודמת לכתיבה - מסמך
    שכבר אושר לא מקבל אפקט שני (P5), וגרסה מיושנת לא עוברת מצב בכלל."""
    assert_is_current_version(document.is_latest, target)
    assert_transition_allowed(document.status, target)
    before = document.status.value
    document.status = target
    now = utcnow()
    if target == DocumentStatus.SENT:
        document.sent_at = now
    elif target == DocumentStatus.ACKNOWLEDGED:
        document.acknowledged_at = now
        document.acknowledged_by_user_id = current_user.user_id
    record_audit_event(db, "Document", document.document_id, action, current_user.user_id,
                       before={"status": before}, after={"status": target.value})
    db.commit()
    db.refresh(document)
    return document


def _download_document_response(db: Session, document: Document, current_user: User):
    full_path = DOCUMENT_STORE_DIR / document.file_path
    if not full_path.exists():
        raise HTTPException(status_code=500, detail="Document file is missing from storage")
    record_audit_event(db, "Document", document.document_id, "DOWNLOADED", current_user.user_id, after={})
    db.commit()
    return FileResponse(str(full_path), media_type="application/pdf",
                        filename=f"{document.template_type}_{document.grant_id}_v{document.version}.pdf")


# --- ADMIN ---------------------------------------------------------

@router.post("/admin/documents", response_model=DocumentOut)
def generate_document(payload: GenerateDocumentRequest,
                      current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                      db: Session = Depends(get_db)):
    if payload.template_type not in DOCUMENT_TEMPLATE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported template_type: {payload.template_type}")

    grant = db.query(Grant).filter(Grant.grant_id == payload.grant_id).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")

    company_id = _company_id_of_grant(db, grant)
    if company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot generate a document for a grant outside your company")

    employee = db.query(Employee).filter(Employee.employee_id == grant.employee_id).first()
    company = db.query(Company).filter(Company.company_id == company_id).first()
    trustee = db.query(Trustee).filter(Trustee.trustee_id == grant.trustee_id).first() if grant.trustee_id else None

    # גרסה: אם כבר קיים מסמך "אחרון" מאותו סוג לאותו מענק, הוא מפסיק להיות
    # is_latest (לא נמחק, לא נדרס) - החלטת התכנון המפורשת של v0.9.0.
    previous_latest = (
        db.query(Document)
        .filter(Document.grant_id == grant.grant_id, Document.template_type == payload.template_type,
                Document.is_latest == True)  # noqa: E712
        .first()
    )
    next_version = (previous_latest.version + 1) if previous_latest else 1
    document_id = generate_uuid()

    try:
        builder = TEMPLATE_BUILDERS[payload.template_type]
        relative_path, file_hash = builder(grant, employee, company, trustee, document_id, next_version)
    except MissingDocumentDataError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except DocumentRenderingError as e:
        # כשל תשתיתי (למשל אין גופן יוניקוד) - 500, לא 409: זו לא בעיה בנתוני
        # המענק ואין שום דבר שהאדמין יכול לתקן בצד שלו.
        raise HTTPException(status_code=500, detail=str(e))

    if previous_latest:
        previous_latest.is_latest = False

    doc = Document(
        document_id=document_id, template_type=payload.template_type, grant_id=grant.grant_id,
        company_id=company_id, employee_id=grant.employee_id, trustee_id=grant.trustee_id,
        status=DocumentStatus.DRAFT, version=next_version, is_latest=True,
        file_path=relative_path, file_sha256=file_hash, created_by_user_id=current_user.user_id,
    )
    db.add(doc)
    record_audit_event(db, "Document", document_id, "GENERATED", current_user.user_id,
                       after={"template_type": payload.template_type, "grant_id": grant.grant_id,
                             "version": next_version, "file_sha256": file_hash})
    db.commit()
    db.refresh(doc)
    return _document_out(db, doc)


@router.get("/admin/documents", response_model=List[DocumentOut])
def list_company_documents(status: Optional[str] = None, template_type: Optional[str] = None,
                           current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                           db: Session = Depends(get_db)):
    """ספריית המסמכים של החברה. ההיקף נקבע מ-current_user.company_id בלבד -
    אין פרמטר company_id שהלקוח יכול לדרוס."""
    query = db.query(Document).filter(Document.company_id == current_user.company_id)
    if status:
        if status not in {s.value for s in DocumentStatus}:
            raise HTTPException(status_code=400, detail=f"Unknown status: {status}")
        query = query.filter(Document.status == DocumentStatus(status))
    if template_type:
        query = query.filter(Document.template_type == template_type)
    return _documents_out(db, query.order_by(Document.generated_at.desc()).all())


@router.post("/admin/documents/{document_id}/send", response_model=DocumentOut)
def send_document_for_acknowledgment(document_id: str,
                                    current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                                    db: Session = Depends(get_db)):
    document = _load_document_or_404(db, document_id, current_user)
    if not document.is_latest:
        raise HTTPException(status_code=409,
                            detail="This is a superseded version - send the latest version instead")
    document = _transition_document(db, document, DocumentStatus.SENT, current_user, "SENT_FOR_ACKNOWLEDGMENT")
    return _document_out(db, document)


@router.get("/admin/documents/{document_id}/download")
def download_document_admin(document_id: str,
                            current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                            db: Session = Depends(get_db)):
    document = _load_document_or_404(db, document_id, current_user)
    return _download_document_response(db, document, current_user)


# --- EMPLOYEE ------------------------------------------------------

@router.get("/employee/documents", response_model=List[DocumentOut])
def list_my_documents(current_user: User = Depends(require_roles(UserRole.EMPLOYEE)),
                      db: Session = Depends(get_db)):
    """רק המסמכים של העובד המחובר, ורק כאלה שנשלחו לו בפועל - טיוטה שהאדמין
    עוד לא שלח אינה אמורה להיות גלויה לעובד."""
    return _documents_out(db, (
        db.query(Document)
        .filter(Document.employee_id == current_user.employee_id,
                Document.status != DocumentStatus.DRAFT)
        .order_by(Document.generated_at.desc())
    ).all())


@router.get("/employee/documents/{document_id}/download")
def download_document_employee(document_id: str,
                               current_user: User = Depends(require_roles(UserRole.EMPLOYEE)),
                               db: Session = Depends(get_db)):
    document = _load_document_or_404(db, document_id, current_user)
    if document.status == DocumentStatus.DRAFT:
        raise HTTPException(status_code=403, detail="This document has not been sent to you yet")
    return _download_document_response(db, document, current_user)


@router.post("/employee/documents/{document_id}/acknowledge", response_model=DocumentOut)
def acknowledge_document_employee(document_id: str,
                                  current_user: User = Depends(require_roles(UserRole.EMPLOYEE)),
                                  db: Session = Depends(get_db)):
    """*** אישור קבלה פנימי, לא חתימה משפטית *** - ראו models.py.Document."""
    document = _load_document_or_404(db, document_id, current_user)
    document = _transition_document(db, document, DocumentStatus.ACKNOWLEDGED, current_user, "ACKNOWLEDGED")
    return _document_out(db, document)


@router.post("/employee/documents/{document_id}/decline", response_model=DocumentOut)
def decline_document_employee(document_id: str,
                              current_user: User = Depends(require_roles(UserRole.EMPLOYEE)),
                              db: Session = Depends(get_db)):
    document = _load_document_or_404(db, document_id, current_user)
    document = _transition_document(db, document, DocumentStatus.DECLINED, current_user, "DECLINED")
    return _document_out(db, document)


# --- TRUSTEE -------------------------------------------------------

@router.get("/trustee/documents/pending", response_model=List[DocumentOut])
def list_pending_documents_trustee(current_user: User = Depends(require_roles(UserRole.TRUSTEE)),
                                   db: Session = Depends(get_db)):
    """תור האישורים הממתינים של הנאמן - רק מסמכים על מענקים שהוא הנאמן שלהם,
    ורק כאלה שממתינים בפועל.

    is_latest נכנס לסינון כאן ולא רק בתצוגה: זה *תור פעולה*, ומסמך שהחברה
    כבר החליפה בגרסה חדשה אינו פעולה שממתינה - השרת דוחה אישור עליו ממילא
    (assert_is_current_version). בפורטל העובד ההתנהגות שונה בכוונה: שם הרשימה
    היא היסטוריה, ולכן גרסה מיושנת נשארת מוצגת ומסומנת ככזו."""
    return _documents_out(db, (
        db.query(Document)
        .filter(Document.trustee_id == current_user.trustee_id,
                Document.status == DocumentStatus.SENT,
                Document.is_latest == True)  # noqa: E712
        .order_by(Document.generated_at.desc())
    ).all())


@router.get("/trustee/documents/{document_id}/download")
def download_document_trustee(document_id: str,
                              current_user: User = Depends(require_roles(UserRole.TRUSTEE)),
                              db: Session = Depends(get_db)):
    document = _load_document_or_404(db, document_id, current_user)
    if document.status == DocumentStatus.DRAFT:
        raise HTTPException(status_code=403, detail="This document has not been sent for acknowledgment yet")
    return _download_document_response(db, document, current_user)


@router.post("/trustee/documents/{document_id}/acknowledge", response_model=DocumentOut)
def acknowledge_document_trustee(document_id: str,
                                 current_user: User = Depends(require_roles(UserRole.TRUSTEE)),
                                 db: Session = Depends(get_db)):
    """*** אישור קבלה פנימי, לא חתימה משפטית *** - ראו models.py.Document."""
    document = _load_document_or_404(db, document_id, current_user)
    document = _transition_document(db, document, DocumentStatus.ACKNOWLEDGED, current_user, "ACKNOWLEDGED")
    return _document_out(db, document)


@router.post("/trustee/documents/{document_id}/decline", response_model=DocumentOut)
def decline_document_trustee(document_id: str,
                             current_user: User = Depends(require_roles(UserRole.TRUSTEE)),
                             db: Session = Depends(get_db)):
    document = _load_document_or_404(db, document_id, current_user)
    document = _transition_document(db, document, DocumentStatus.DECLINED, current_user, "DECLINED")
    return _document_out(db, document)
