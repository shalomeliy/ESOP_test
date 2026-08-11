from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Employee, OptionPool, Trustee, User, UserRole, UserSession, Company
from backend.app.schemas import CompanyUpdateRequest, CompanyOut
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
              "total_authorized_shares": comp.total_authorized_shares}
    if payload.name is not None:
        comp.name = payload.name
    if payload.country_code is not None:
        comp.country_code = payload.country_code
    if payload.total_authorized_shares is not None:
        comp.total_authorized_shares = payload.total_authorized_shares
    record_audit_event(db, "Company", comp.company_id, "UPDATE", current_user.user_id,
                        before=before, after={"name": comp.name, "country_code": comp.country_code,
                                              "total_authorized_shares": comp.total_authorized_shares})
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
