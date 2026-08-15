from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.types import business_today
from backend.app.models import Employee, User, UserRole
from backend.app.services.engine import DeterministicESOPEngine, MissingVestingScheduleError
from backend.app.schemas import EmployeeDashboardOut
from backend.app.auth import require_roles
from backend.app.api.exercise_requests import _vested_at, _trustee_holding_status

router = APIRouter()


# ===================================================================
# EMPLOYEE PORTAL
# ===================================================================

@router.get("/employee/dashboard/{employee_id}", response_model=EmployeeDashboardOut)
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
        is_trustee_met, end_date = _trustee_holding_status(grant, today)
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
            "holding_period_end_date": str(end_date) if end_date else None,
            "is_within_post_termination_window": is_within_ptw,
            "post_termination_exercise_deadline": str(ptw_deadline) if ptw_deadline else None,
        })

    return {"employee_name": f"{employee.first_name} {employee.last_name}", "grants": grants_data}
