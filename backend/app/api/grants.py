import json
from datetime import date
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import OptionPool, Grant, Employee, Trustee, VestingSchedule, User, UserRole
from backend.app.schemas import (
    PoolOut, GrantOut, CreateGrantRequest, CreateGrantResponse,
    VestingPauseRequest, VestingPauseResponse,
)
from backend.app.services.engine import shift_months
from backend.app.services.audit import record_audit_event
from backend.app.services.ledger import append_event, events_for, record_ownership
from backend.app.auth import require_roles

router = APIRouter()


# ===================================================================
# ולידציות משותפות - יושבות כאן ולא בכל endpoint בנפרד, כי אותה בדיקה
# חסרה קודם בשני נתיבי אישור שונים (admin ו-trustee) ובנתיב ההגשה.
# ===================================================================

# גיל מינימלי להענקת אופציות. ⚠️ ברירת מחדל שמרנית ברמת המערכת ולא כלל מאומת:
# כשירות משפטית של קטין לחתום על כתב הענקה היא שאלה משפטית שטרם אומתה מול מקור.
# הכיוון נבחר כך שהמערכת *חוסמת* במקום להעניק בשקט מענק שאולי אינו אכיף.
MINIMUM_GRANT_AGE_YEARS = 18


@router.get("/admin/pools", response_model=List[PoolOut])
def list_pools(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    return db.query(OptionPool).filter(OptionPool.company_id == current_user.company_id).all()


@router.get("/admin/grants", response_model=List[GrantOut])
def list_company_grants(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    pool_ids = [p.pool_id for p in db.query(OptionPool.pool_id).filter(OptionPool.company_id == current_user.company_id).all()]
    return db.query(Grant).filter(Grant.pool_id.in_(pool_ids)).all()


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
