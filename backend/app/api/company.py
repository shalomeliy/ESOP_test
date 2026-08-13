from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import (
    DOCUMENT_TEMPLATE_TYPES, DocumentAcknowledgmentWindowOverride, Employee,
    OptionPool, Trustee, User, UserRole, UserSession, Company,
)
from backend.app.schemas import (
    CompanyUpdateRequest, CompanyOut, DocumentAcknowledgmentWindowOverrideOut,
    DocumentAcknowledgmentWindowOverrideUpsertRequest,
)
from backend.app.services.audit import record_audit_event
from backend.app.auth import require_roles

router = APIRouter()


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
    before = {"name": comp.name, "country_code": comp.country_code,
              "total_authorized_shares": comp.total_authorized_shares,
              "acknowledgment_window_days": comp.acknowledgment_window_days}
    if payload.name is not None:
        comp.name = payload.name
    if payload.country_code is not None:
        comp.country_code = payload.country_code
    if payload.total_authorized_shares is not None:
        comp.total_authorized_shares = payload.total_authorized_shares
    if payload.acknowledgment_window_days is not None:
        # is not None אינו שקול ל"ערך תקין" - ראו models.py.Company. כאן, בשונה
        # מ-total_authorized_shares, יש CHECK ברמת ה-DB (ck_companies_
        # acknowledgment_window_days_positive) שהיה מפיל IntegrityError גולמי
        # (500) בלי הבדיקה הזו - 400 נקי לפני הכתיבה עדיף.
        if payload.acknowledgment_window_days <= 0:
            raise HTTPException(status_code=400, detail="acknowledgment_window_days must be positive")
        comp.acknowledgment_window_days = payload.acknowledgment_window_days
    record_audit_event(db, "Company", comp.company_id, "UPDATE", current_user.user_id,
                        before=before, after={"name": comp.name, "country_code": comp.country_code,
                                              "total_authorized_shares": comp.total_authorized_shares,
                                              "acknowledgment_window_days": comp.acknowledgment_window_days})
    db.commit()
    db.refresh(comp)
    return comp


# ===================================================================
# חלון אישור קבלה פר-סוג-מסמך (v1.0.2, HANDOFF.md debt item 2) - שכבה שנייה
# מעל acknowledgment_window_days הכללי-לחברה שממש מעליו (update_my_company).
# ===================================================================

@router.get("/admin/company/acknowledgment-windows",
           response_model=list[DocumentAcknowledgmentWindowOverrideOut])
def list_acknowledgment_window_overrides(
        current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    """מחזיר רק את ה-overrides שקיימים בפועל - סוג מסמך בלי שורה כאן משתמש
    בברירת המחדל של החברה (או הגלובלית). הלקוח כבר מכיר את רשימת סוגי
    המסמכים המלאה (DOCUMENT_TEMPLATE_TYPES, לא FK), אז אין צורך להחזיר שורה
    ריקה לכל סוג שלא הוגדר."""
    return (
        db.query(DocumentAcknowledgmentWindowOverride)
        .filter(DocumentAcknowledgmentWindowOverride.company_id == current_user.company_id)
        .all()
    )


@router.put("/admin/company/acknowledgment-windows/{template_type}",
           response_model=DocumentAcknowledgmentWindowOverrideOut)
def upsert_acknowledgment_window_override(
        template_type: str, payload: DocumentAcknowledgmentWindowOverrideUpsertRequest,
        current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    """window_days=None מוחק את ה-override (חזרה לירושה מ-Company.acknowledgment_window_days) -
    אותה מוסכמה בדיוק כמו שאר שדות ה-override בפרויקט הזה. ערך חיובי קובע/מעדכן אותו."""
    if template_type not in DOCUMENT_TEMPLATE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported template_type: {template_type}")

    existing = (
        db.query(DocumentAcknowledgmentWindowOverride)
        .filter(DocumentAcknowledgmentWindowOverride.company_id == current_user.company_id,
               DocumentAcknowledgmentWindowOverride.template_type == template_type)
        .first()
    )

    if payload.window_days is None:
        if existing is not None:
            record_audit_event(db, "DocumentAcknowledgmentWindowOverride", existing.override_id, "DELETE",
                               current_user.user_id,
                               before={"template_type": template_type, "window_days": existing.window_days})
            db.delete(existing)
            db.commit()
        return {"template_type": template_type, "window_days": None}

    # is not None אינו שקול ל"ערך תקין" - ראו models.py.Company/update_my_company
    # למעלה. אותו לקח בדיוק: יש CHECK ברמת ה-DB
    # (ck_doc_ack_window_override_positive) שהיה מפיל IntegrityError גולמי
    # (500) בלי הבדיקה הזו - 400 נקי לפני הכתיבה עדיף.
    if payload.window_days <= 0:
        raise HTTPException(status_code=400, detail="window_days must be positive")

    if existing is not None:
        before = {"window_days": existing.window_days}
        existing.window_days = payload.window_days
        record_audit_event(db, "DocumentAcknowledgmentWindowOverride", existing.override_id, "UPDATE",
                           current_user.user_id, before=before, after={"window_days": existing.window_days})
        db.commit()
        db.refresh(existing)
        return existing

    new_override = DocumentAcknowledgmentWindowOverride(
        company_id=current_user.company_id, template_type=template_type, window_days=payload.window_days,
    )
    db.add(new_override)
    db.flush()
    record_audit_event(db, "DocumentAcknowledgmentWindowOverride", new_override.override_id, "CREATE",
                       current_user.user_id,
                       after={"template_type": template_type, "window_days": payload.window_days})
    db.commit()
    db.refresh(new_override)
    return new_override


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
