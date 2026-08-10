from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Employee, Grant, OptionPool, ExerciseRequest, AuditLog, User, UserRole
from backend.app.schemas import AuditLogOut
from backend.app.auth import require_roles

router = APIRouter()


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
