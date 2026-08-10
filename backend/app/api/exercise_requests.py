from datetime import date
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.types import utcnow, business_today
from backend.app.models import (
    Grant, OptionPool, StockPricesHistory, User, UserRole, ExerciseRequest, ExerciseRequestStatus,
)
from backend.app.schemas import (
    ExerciseSimulationRequest, ExerciseSimulationResponse,
    ExerciseRequestCreate, ExerciseRequestReview, ExerciseRequestOut,
)
from backend.app.services.engine import DeterministicESOPEngine, MissingVestingScheduleError
from backend.app.services.tax_engine import TaxCalculationEngine, MissingTaxRuleError
from backend.app.services.audit import record_audit_event
from backend.app.services.ledger import append_event, record_ownership
from backend.app.auth import require_roles

router = APIRouter()


# ===================================================================
# ולידציות משותפות - יושבות כאן ולא בכל endpoint בנפרד, כי אותה בדיקה
# חסרה קודם בשני נתיבי אישור שונים (admin ו-trustee) ובנתיב ההגשה.
# ===================================================================

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
