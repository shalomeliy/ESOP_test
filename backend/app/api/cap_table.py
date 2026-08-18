from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import (
    Company, Employee, ShareClass, Shareholder, ShareIssuance, User, UserRole, generate_uuid,
)
from backend.app.schemas import (
    CreateShareClassRequest, ShareClassOut,
    CreateShareholderRequest, ShareholderOut,
    CreateShareIssuanceRequest, ShareIssuanceOut,
    CapTableSnapshotOut,
    BuybackRequest, ExecuteBuybackRequest, BuybackPreviewOut, BuybackReceiptOut,
)
from backend.app.services.audit import record_audit_event
from backend.app.services.buyback import BuybackRejected, build_buyback_projection
from backend.app.services.cap_table import compute_cap_table_snapshot
from backend.app.services.ledger import append_event, record_ownership
from backend.app.auth import require_roles

router = APIRouter()


# ===================================================================
# בעלות על ישות טבלת הון - נקודה אחת, v1.2.0.
#
# עד כאן הבדיקה הועתקה ביד בכל handler (שלוש פעמים), וזו הצורה שהולידה שתי
# בעיות IDOR קודמות: העתקה רביעית ששוכחת שורה אחת אינה נראית בשום בדיקה.
#
# *** 404 ולא 403 ***: הצורה הישנה החזירה 404 ל"לא נמצא" ו-403 ל"קיים בחברה
# אחרת" - כלומר אורקל קיום חוצה-טננטים: מי שמנחש מזהים למד מהקוד אילו מהם
# אמיתיים. שני המקרים מוחזרים עכשיו זהים.
#
# הסינון הוא על עמודת company_id *הישירה* של הישות, לעולם לא דרך join -
# ראו models.py.ShareIssuance.company_id.
# ===================================================================

def _get_owned_or_404(db: Session, model, pk: str, company_id: str, label: str):
    row = db.get(model, pk)
    if row is None or row.company_id != company_id:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return row


def get_owned_shareholder(db: Session, shareholder_id: str, company_id: str) -> Shareholder:
    return _get_owned_or_404(db, Shareholder, shareholder_id, company_id, "Shareholder")


def get_owned_share_class(db: Session, share_class_id: str, company_id: str) -> ShareClass:
    return _get_owned_or_404(db, ShareClass, share_class_id, company_id, "Share class")


def get_owned_issuance(db: Session, share_issuance_id: str, company_id: str) -> ShareIssuance:
    return _get_owned_or_404(db, ShareIssuance, share_issuance_id, company_id, "Share issuance")


# ===================================================================
# טבלת הון (Cap Table) - סוגי מניות, בעלי מניות, הקצאות מניות (v1.0.0 שלב א)
#
# ShareClass/Shareholder הם דאטת-ייחוס רגילה (reference data) - נכתבים ישירות
# בלי ledger, החלטת התכנון המפורשת (הם לא בי-טמפורליים: אין להם "מצב שמצטבר
# מעל בסיס", רק תיאור קבוע). ShareIssuance הוא ledger-native מהיום הראשון -
# ראו models.py.ShareIssuance.
#
# COMPANY_ADMIN-only בשלב א: אין עדיין RBAC דק יותר (רואה-חשבון קריאה-בלבד
# וכו') לפני v1.3.0 - זה ברירת המחדל הבטוחה היחידה כרגע (ראו התכנון).
# ===================================================================


@router.post("/admin/share-classes", response_model=ShareClassOut)
def create_share_class(payload: CreateShareClassRequest,
                       current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                       db: Session = Depends(get_db)):
    share_class = ShareClass(
        company_id=current_user.company_id,
        name=payload.name,
        class_type=payload.class_type,
        seniority_order=payload.seniority_order,
    )
    db.add(share_class)
    db.flush()  # share_class.share_class_id זמין מכאן
    record_audit_event(db, "ShareClass", share_class.share_class_id, "CREATE", current_user.user_id,
                        after={"name": share_class.name, "class_type": share_class.class_type,
                              "seniority_order": share_class.seniority_order})
    db.commit()
    db.refresh(share_class)
    return share_class


@router.get("/admin/share-classes", response_model=List[ShareClassOut])
def list_share_classes(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    return db.query(ShareClass).filter(ShareClass.company_id == current_user.company_id).all()


@router.post("/admin/shareholders", response_model=ShareholderOut)
def create_shareholder(payload: CreateShareholderRequest,
                       current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                       db: Session = Depends(get_db)):
    # employee_id הוא FK אופציונלי (בעל מניות לא חייב להיות עובד) - נבדק
    # מפורשות רק כשניתן, לפני ה-insert: גם קיום השורה (מונע IntegrityError
    # גולמי שחוזר כ-500) וגם שיוך לחברה הנכונה (מונע קישור בעל-מניות של
    # חברה A לעובד של חברה B). אותו דפוס בדיוק כמו בדיקת shareholder/share_class
    # ב-create_share_issuance למעלה.
    #
    # *** 404 אחיד גם כאן (סקירה 12, אזהרה 6) ***: עד כאן "עובד לא קיים" החזיר
    # 404 ו"עובד של חברה אחרת" 403 - כלומר אורקל קיום עובד, שאפשר למנות בו את
    # המזהים הזרועים (EMP-001, EMP-TAX-WORKINCOME-1) בבקשה אחת למזהה. אותו פגם
    # שהגרסה הזו סגרה בשלוש ישויות טבלת ההון, על ישות Employee. שני הענפים
    # מחזירים עכשיו גוף זהה לתו.
    if payload.employee_id is not None:
        employee = db.query(Employee).filter(Employee.employee_id == payload.employee_id).first()
        if employee is None or employee.company_id != current_user.company_id:
            raise HTTPException(status_code=404, detail="Employee not found")

    shareholder = Shareholder(
        company_id=current_user.company_id,
        name=payload.name,
        shareholder_type=payload.shareholder_type,
        employee_id=payload.employee_id,
    )
    db.add(shareholder)
    db.flush()  # shareholder.shareholder_id זמין מכאן
    record_audit_event(db, "Shareholder", shareholder.shareholder_id, "CREATE", current_user.user_id,
                        after={"name": shareholder.name, "shareholder_type": shareholder.shareholder_type,
                              "employee_id": shareholder.employee_id})
    db.commit()
    db.refresh(shareholder)
    return shareholder


@router.get("/admin/shareholders", response_model=List[ShareholderOut])
def list_shareholders(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    return db.query(Shareholder).filter(Shareholder.company_id == current_user.company_id).all()


@router.post("/admin/share-issuances", response_model=ShareIssuanceOut)
def create_share_issuance(payload: CreateShareIssuanceRequest,
                          current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                          db: Session = Depends(get_db)):
    """הקצאת מניות ל-Shareholder קיים - ledger-native (ראו models.py.ShareIssuance).
    issue_date הוא קלט מפורש מהקורא ולא מהשעון, כדי שהזנת נתונים היסטוריים
    ו-snapshot-לפי-תאריך עתידי (שלב ב) יהיו נכונים."""
    # v1.2.0: שתי הבדיקות עברו לתלות המשותפת למעלה, ושתיהן מחזירות עכשיו 404
    # אחיד - "לא קיים" ו"קיים בחברה אחרת" חייבים להיראות זהים מבחוץ.
    get_owned_shareholder(db, payload.shareholder_id, current_user.company_id)
    get_owned_share_class(db, payload.share_class_id, current_user.company_id)

    if payload.shares <= 0:
        raise HTTPException(status_code=400, detail="shares must be positive")

    # תקרת מניות מאושרות - נבדקת רק כש-Company.total_authorized_shares מוגדר
    # (לא None). אותו דפוס בדיוק כמו בדיקת קיבולת הפול ב-grants.py::create_grant -
    # אין להמציא תקרה שלא הוגדרה במפורש.
    company = db.query(Company).filter(Company.company_id == current_user.company_id).first()
    if company and company.total_authorized_shares is not None:
        existing_total = (
            db.query(func.sum(ShareIssuance.shares))
            .filter(ShareIssuance.company_id == current_user.company_id)
            .scalar()
        ) or 0.0
        if existing_total + payload.shares > company.total_authorized_shares:
            raise HTTPException(
                status_code=400,
                detail=(f"Issuance would exceed total_authorized_shares "
                        f"(available: {company.total_authorized_shares - existing_total})"),
            )

    share_issuance = ShareIssuance(
        company_id=current_user.company_id,
        shareholder_id=payload.shareholder_id,
        share_class_id=payload.share_class_id,
        shares=payload.shares,
        issue_date=payload.issue_date,
    )
    db.add(share_issuance)
    db.flush()  # share_issuance.share_issuance_id זמין מכאן

    record_ownership(db, aggregate_id=share_issuance.share_issuance_id, aggregate_type="ShareIssuance",
                     company_id=current_user.company_id)
    append_event(db, event_type="SHARE_ISSUANCE_ESTABLISHED", aggregate_type="ShareIssuance",
                aggregate_id=share_issuance.share_issuance_id,
                payload={"shares": share_issuance.shares, "shareholder_id": share_issuance.shareholder_id,
                        "share_class_id": share_issuance.share_class_id, "issue_date": share_issuance.issue_date},
                effective_date=payload.issue_date, actor_user_id=current_user.user_id)

    record_audit_event(db, "ShareIssuance", share_issuance.share_issuance_id, "CREATE", current_user.user_id,
                        after={"shareholder_id": share_issuance.shareholder_id,
                              "share_class_id": share_issuance.share_class_id,
                              "shares": share_issuance.shares, "issue_date": share_issuance.issue_date})

    db.commit()
    db.refresh(share_issuance)
    return share_issuance


@router.get("/admin/share-issuances", response_model=List[ShareIssuanceOut])
def list_share_issuances(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    return db.query(ShareIssuance).filter(ShareIssuance.company_id == current_user.company_id).all()


@router.get("/admin/cap-table/snapshot", response_model=CapTableSnapshotOut)
def get_cap_table_snapshot(as_of: Optional[date] = None,
                           current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                           db: Session = Depends(get_db)):
    """דילול (outstanding + fully-diluted) נכון לתאריך as_of (ברירת מחדל:
    היום העסקי) - אגרגציה בזמן קריאה בלבד, ראו services/cap_table.py.
    as_of לא חוקי (למשל מחרוזת שאינה תאריך) נדחה כבר ע"י FastAPI/Pydantic
    (422) - אין פענוח תאריך ידני כאן."""
    return compute_cap_table_snapshot(db, current_user.company_id, as_of)


# ===================================================================
# רכישה עצמית / תיקון הנפקה - v1.2.0. מפרט: docs/spec/v1.2.0.md.
#
# שני האנדפוינטים קוראים ל-*אותה* build_buyback_projection. תצוגה מקדימה
# שמריצה ולידציה משלה הייתה שער שני וחלש יותר (§7).
# ===================================================================

@router.post("/admin/cap-table/buyback/preview", response_model=BuybackPreviewOut)
def preview_buyback(payload: BuybackRequest,
                    current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                    db: Session = Depends(get_db)):
    """הדיף המלא לפני שנכתב משהו. *** אינו כותב דבר *** - אין commit בנתיב
    הזה, וקריאה ואז נטישה משאירה אפס אירועים חדשים (קריטריון 9)."""
    issuance = get_owned_issuance(db, payload.share_issuance_id, current_user.company_id)
    try:
        return build_buyback_projection(db, issuance=issuance, shares=payload.shares,
                                        effective_date=payload.effective_date, reason=payload.reason)
    except BuybackRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/admin/cap-table/buyback", response_model=BuybackReceiptOut)
def execute_buyback(payload: ExecuteBuybackRequest,
                    current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                    db: Session = Depends(get_db)):
    """מבצע. אירוע ה-ledger והפחתת העמודה קורים ב*אותה טרנזקציה* - זו התבנית
    של OptionPool.allocated_shares, והיא מה שמונע סטייה בין העמודה ל-ledger.

    *** אין כאן מספרים מהדפדפן ***: הפרויקציה מחושבת מחדש מהמקור, והבקשה
    נדחית אם המנה זזה מאז התצוגה המקדימה (קריטריון 10)."""
    if payload.confirm_shares != payload.shares:
        raise HTTPException(status_code=400,
                            detail="confirm_shares does not match shares - the amount must be re-typed exactly")

    issuance = get_owned_issuance(db, payload.share_issuance_id, current_user.company_id)

    try:
        projection = build_buyback_projection(db, issuance=issuance, shares=payload.shares,
                                              effective_date=payload.effective_date, reason=payload.reason)
    except BuybackRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 409 ולא 400: הבקשה עצמה תקינה, המצב מתחתיה זז. ההבחנה חשובה ללקוח -
    # 409 אומר "רענן את התצוגה ונסה שוב", 400 אומר "הבקשה שגויה".
    if projection["expected_sequence_no"] != payload.expected_sequence_no:
        raise HTTPException(
            status_code=409,
            detail=(f"the lot changed since the preview was taken "
                    f"(expected sequence {payload.expected_sequence_no}, "
                    f"current {projection['expected_sequence_no']}) - re-run the preview"),
        )

    before_shares = issuance.shares
    issuance.shares = projection["lot_after"]

    # מפתח הקורלציה של הביצוע (מפרט §7). ביצוע אחד כותב היום אירוע אחד, ולכן
    # הוא אינו מקשר דבר *כרגע* - אבל ledger הוא append-only: אירוע שנכתב בלי
    # המפתח לא יקבל אותו לעולם, ואירועי v1.2.1 (מיזוג/גיוס/העברה), שכן כותבים
    # כמה אירועים בביצוע אחד, לא יוכלו להתקשר לאירועי v1.2.0 שנכתבו בינתיים.
    # לכן הוא נטבע מהאירוע הראשון (הכרעת המשתתף, 17/08/2026).
    company_event_id = generate_uuid()

    event = append_event(
        db, event_type="SHARE_ISSUANCE_ADJUSTED", aggregate_type="ShareIssuance",
        aggregate_id=issuance.share_issuance_id,
        payload={"delta_shares": projection["lot_delta"], "reason": payload.reason,
                 "company_event_id": company_event_id},
        effective_date=payload.effective_date, actor_user_id=current_user.user_id,
    )

    # פעולה הרסנית חייבת לרשום before *וגם* after - record_audit_event תומך
    # בשניהם, ושלושת מסלולי טבלת ההון הקיימים העבירו after בלבד.
    record_audit_event(db, "ShareIssuance", issuance.share_issuance_id, "UPDATE", current_user.user_id,
                       before={"shares": before_shares},
                       after={"shares": issuance.shares, "reason": payload.reason,
                              "effective_date": payload.effective_date})

    # *** שני ביצועים במקביל (סקירה 12, אזהרה 5) ***: שניהם קוראים את אותו
    # max(sequence_no), שניהם עוברים את בדיקת ה-409 למעלה, ושניהם מנסים להכניס
    # N+1. ה-UniqueConstraint(aggregate_id, sequence_no) מונע את ההפחתה הכפולה -
    # זו רשת הביטחון האמיתית - אבל הוא זורק IntegrityError, ובלי הלכידה הזו
    # נתיב הכתיבה החזיר 500 שהלקוח אינו יכול להבחין בינו לתקלת שרת ועלול לנסות
    # שוב. אותו 409 ואותו טקסט "re-run the preview" כמו במסלול המיושן הסדרתי:
    # מבחינת הקורא זה בדיוק אותו מצב - המנה זזה מתחתיו.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=("the lot changed since the preview was taken "
                    "(a concurrent adjustment was written first) - re-run the preview"),
        )

    return {
        "ledger_event_id": event.event_id,
        "company_event_id": company_event_id,
        "share_issuance_id": issuance.share_issuance_id,
        "lot_after": projection["lot_after"],
        "company_as_of": projection["company_as_of"],
        "company_after": projection["company_after"],
        "tax_treatment": projection["tax_treatment"],
        "tax_reason_code": projection["tax_reason_code"],
    }
