from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Company, Employee, ShareClass, Shareholder, ShareIssuance, User, UserRole
from backend.app.schemas import (
    CreateShareClassRequest, ShareClassOut,
    CreateShareholderRequest, ShareholderOut,
    CreateShareIssuanceRequest, ShareIssuanceOut,
    CapTableSnapshotOut,
)
from backend.app.services.audit import record_audit_event
from backend.app.services.cap_table import compute_cap_table_snapshot
from backend.app.services.ledger import append_event, record_ownership
from backend.app.auth import require_roles

router = APIRouter()


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
    if payload.employee_id is not None:
        employee = db.query(Employee).filter(Employee.employee_id == payload.employee_id).first()
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")
        if employee.company_id != current_user.company_id:
            raise HTTPException(status_code=403, detail="Cannot link shareholder to an employee outside your company")

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
    shareholder = db.query(Shareholder).filter(Shareholder.shareholder_id == payload.shareholder_id).first()
    if not shareholder:
        raise HTTPException(status_code=404, detail="Shareholder not found")
    if shareholder.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot issue shares to a shareholder outside your company")

    share_class = db.query(ShareClass).filter(ShareClass.share_class_id == payload.share_class_id).first()
    if not share_class:
        raise HTTPException(status_code=404, detail="Share class not found")
    if share_class.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot issue shares of a class outside your company")

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
