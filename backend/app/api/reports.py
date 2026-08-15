import io
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import SAVED_REPORT_TYPES, SavedReport, User, UserRole
from backend.app.schemas import (
    SavedReportCreateRequest, SavedReportOut, ReportEnvelopeOut, ReportsDashboardOut,
    SavedReportDeleteOut,
)
from backend.app.services.audit import record_audit_event
from backend.app.services.company_scope import build_company_scope
from backend.app.services.reports import (
    REPORT_TITLES, build_asc718_readiness, build_compensation_expense, build_dashboard,
    build_deadline_risk, build_exercise_activity, build_movement, build_pool_status,
    build_trustee_exposure, rows_to_csv_bytes, rows_to_pdf_bytes,
)
from backend.app.types import business_today, utcnow
from backend.app.auth import require_roles
import json

router = APIRouter()

# ===================================================================
# דוחות, ייצוא ו-BI (v1.1.0) - כל שבעת ה-endpoints הבאים admin-only
# (COMPANY_ADMIN, בדיוק כמו cap_table.py/export.py - אין עדיין RBAC דק
# יותר לפני v1.5.0). כל תוצאה מחושבת בזמן קריאה בלבד (services/reports.py),
# אין persist של תוצאת דוח - רק קונפיגורציה שמורה (saved_reports, למטה).
#
# **audit חובה על כל בקשה, כולל JSON** - זהו נתיב יציאה חדש בהיקף גדול של
# PII/נתוני שכר (שמות עובדים, שווי מוערך) שלא היה קיים לפני v1.1.0.
# format=json -> action="generated"; format=csv/pdf (הורדת קובץ) ->
# action="downloaded". notes מכיל רק סוג דוח/פורמט/טווח תאריכים - לעולם לא
# שם עובד או נתון גולמי אחר.
# ===================================================================

_VALID_FORMATS = {"json", "csv", "pdf"}

# שבעת הדוחות מחזירים JSON *או* קובץ, תלוי ב-?format. response_model לבדו היה
# מצהיר ב-/docs "application/json" בלבד - כלומר התיעוד היה נעשה *פחות* נכון
# מהמצב שלפניו, שבו לא הצהיר כלום. שני סוגי התוכן מוצהרים כאן במפורש
# (v1.1.1 פריט ד2). ה-StreamingResponse עצמו עובר בלי ולידציה כי FastAPI
# מדלגת על response_model כשה-endpoint מחזיר Response מוכן.
_FORMAT_RESPONSES = {
    200: {"content": {"application/json": {}, "text/csv": {}, "application/pdf": {}}}
}


def _validate_format(fmt: str) -> str:
    if fmt not in _VALID_FORMATS:
        raise HTTPException(status_code=400, detail=f"format must be one of {sorted(_VALID_FORMATS)}")
    return fmt


def _respond(report_type: str, result, fmt: str, current_user: User, db: Session, notes_extra: str = ""):
    action = "generated" if fmt == "json" else "downloaded"
    notes = f"report_type={report_type} format={fmt}"
    if notes_extra:
        notes = f"{notes} {notes_extra}"
    record_audit_event(db, "Report", report_type, action, current_user.user_id, notes=notes)
    db.commit()

    if fmt == "json":
        # columns נשלח במפורש ולא נגזר בלקוח מ-Object.keys(rows[0]): סדר המפתחות
        # בדיקט של השורה וסדר columns הם שני מקורות שחייבים להישאר מסונכרנים ידנית,
        # וכשהם נפרדים טעות מיישרת עמודת כסף מתחת לכותרת של עמודת כסף אחרת - שגיאה
        # שנראית נכונה. CSV/PDF כבר משתמשים ב-columns; עכשיו גם המסך.
        return {
            "report_type": report_type, "generated_at": utcnow(), "columns": result.columns,
            "rows": result.rows, "summary": result.summary, "disclosures": result.disclosures,
        }

    filename_stem = f"{report_type.lower()}_{business_today().isoformat()}"
    if fmt == "csv":
        csv_bytes = rows_to_csv_bytes(result.columns, result.rows)
        return StreamingResponse(
            io.BytesIO(csv_bytes), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename_stem}.csv"'},
        )

    pdf_bytes = rows_to_pdf_bytes(REPORT_TITLES[report_type], result.columns, result.rows, result.disclosures)
    return StreamingResponse(
        io.BytesIO(pdf_bytes), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename_stem}.pdf"'},
    )


@router.get("/admin/reports/pool-status", response_model=ReportEnvelopeOut,
             responses=_FORMAT_RESPONSES)
def report_pool_status(format: str = "json",
                       current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                       db: Session = Depends(get_db)):
    fmt = _validate_format(format)
    scope = build_company_scope(db, current_user.company_id)
    result = build_pool_status(db, scope)
    return _respond("POOL_STATUS", result, fmt, current_user, db)


@router.get("/admin/reports/trustee-exposure", response_model=ReportEnvelopeOut,
             responses=_FORMAT_RESPONSES)
def report_trustee_exposure(format: str = "json",
                            current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                            db: Session = Depends(get_db)):
    fmt = _validate_format(format)
    scope = build_company_scope(db, current_user.company_id)
    result = build_trustee_exposure(db, scope)
    return _respond("TRUSTEE_EXPOSURE", result, fmt, current_user, db)


@router.get("/admin/reports/deadline-risk", response_model=ReportEnvelopeOut,
             responses=_FORMAT_RESPONSES)
def report_deadline_risk(format: str = "json",
                         current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                         db: Session = Depends(get_db)):
    fmt = _validate_format(format)
    scope = build_company_scope(db, current_user.company_id)
    result = build_deadline_risk(db, scope, current_user.company_id, current_user.user_id)
    return _respond("DEADLINE_RISK", result, fmt, current_user, db)


@router.get("/admin/reports/exercise-activity", response_model=ReportEnvelopeOut,
             responses=_FORMAT_RESPONSES)
def report_exercise_activity(format: str = "json", date_from: Optional[date] = None, date_to: Optional[date] = None,
                             current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                             db: Session = Depends(get_db)):
    fmt = _validate_format(format)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be <= date_to")
    scope = build_company_scope(db, current_user.company_id)
    result = build_exercise_activity(db, scope, date_from, date_to)
    return _respond("EXERCISE_ACTIVITY", result, fmt, current_user, db,
                    notes_extra=f"date_from={date_from} date_to={date_to}")


@router.get("/admin/reports/compensation-expense", response_model=ReportEnvelopeOut,
             responses=_FORMAT_RESPONSES)
def report_compensation_expense(format: str = "json",
                                current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                                db: Session = Depends(get_db)):
    fmt = _validate_format(format)
    scope = build_company_scope(db, current_user.company_id)
    result = build_compensation_expense(db, scope)
    return _respond("COMPENSATION_EXPENSE", result, fmt, current_user, db)


@router.get("/admin/reports/movement", response_model=ReportEnvelopeOut,
             responses=_FORMAT_RESPONSES)
def report_movement(date_from: date, date_to: date, format: str = "json",
                    current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                    db: Session = Depends(get_db)):
    fmt = _validate_format(format)
    if date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be <= date_to")
    scope = build_company_scope(db, current_user.company_id)
    result = build_movement(db, scope, date_from, date_to)
    return _respond("MOVEMENT", result, fmt, current_user, db,
                    notes_extra=f"date_from={date_from} date_to={date_to}")


@router.get("/admin/reports/asc718-readiness", response_model=ReportEnvelopeOut,
             responses=_FORMAT_RESPONSES)
def report_asc718_readiness(format: str = "json",
                            current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                            db: Session = Depends(get_db)):
    fmt = _validate_format(format)
    scope = build_company_scope(db, current_user.company_id)
    result = build_asc718_readiness(db, scope)
    return _respond("ASC718_READINESS", result, fmt, current_user, db)


@router.get("/admin/reports/dashboard", response_model=ReportsDashboardOut)
def reports_dashboard(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                      db: Session = Depends(get_db)):
    """JSON בלבד - אין format param, אין CSV/PDF (החלטת תכנון מפורשת, PLAN v1.1.0)."""
    scope = build_company_scope(db, current_user.company_id)
    dashboard = build_dashboard(db, scope)
    record_audit_event(db, "Report", "DASHBOARD", "generated", current_user.user_id,
                       notes="report_type=DASHBOARD format=json")
    db.commit()
    return dashboard


# ===================================================================
# דוחות שמורים (Saved Reports) - CRUD בלבד, לא endpoint שמריץ דוח שמור
# (אין "run" - השמירה היא קונפיגורציה בלבד; ה-UI מטעין אותה חזרה לפילטרים
# ומריץ מול אחד משבעת ה-endpoints למעלה). לכן אין audit על GET כאן - זו
# לא יציאת דאטה עסקי, בניגוד לדוחות עצמם.
# ===================================================================

def _saved_report_out(row: SavedReport) -> SavedReportOut:
    return SavedReportOut(
        report_id=row.report_id, company_id=row.company_id, owner_user_id=row.owner_user_id,
        is_private=row.is_private, name=row.name, report_type=row.report_type,
        filter_params=json.loads(row.filter_params), created_at=row.created_at,
    )


@router.post("/admin/reports/saved", response_model=SavedReportOut)
def create_saved_report(payload: SavedReportCreateRequest,
                        current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                        db: Session = Depends(get_db)):
    if payload.report_type not in SAVED_REPORT_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"report_type must be one of {sorted(SAVED_REPORT_TYPES)}")

    saved = SavedReport(
        company_id=current_user.company_id, owner_user_id=current_user.user_id,
        is_private=payload.is_private, name=payload.name, report_type=payload.report_type,
        filter_params=json.dumps(payload.filter_params, default=str),
    )
    db.add(saved)
    db.flush()  # saved.report_id זמין מכאן
    record_audit_event(db, "SavedReport", saved.report_id, "CREATE", current_user.user_id,
                       after={"name": saved.name, "report_type": saved.report_type,
                             "is_private": saved.is_private})
    db.commit()
    db.refresh(saved)
    return _saved_report_out(saved)


@router.get("/admin/reports/saved", response_model=List[SavedReportOut])
def list_saved_reports(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                       db: Session = Depends(get_db)):
    """own OR (משותף לחברה) - בדיוק כלל הנראות מה-PLAN, לא סינון company_id
    גורף לבד: דוח פרטי של admin אחר באותה חברה לא אמור להופיע כאן."""
    rows = (
        db.query(SavedReport)
        .filter(or_(
            SavedReport.owner_user_id == current_user.user_id,
            and_(SavedReport.is_private.is_(False), SavedReport.company_id == current_user.company_id),
        ))
        .all()
    )
    return [_saved_report_out(r) for r in rows]


@router.get("/admin/reports/saved/{report_id}", response_model=SavedReportOut)
def get_saved_report(report_id: str, current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                     db: Session = Depends(get_db)):
    row = db.query(SavedReport).filter(SavedReport.report_id == report_id).first()
    visible = row is not None and (
        row.owner_user_id == current_user.user_id
        or (row.is_private is False and row.company_id == current_user.company_id)
    )
    if not visible:
        # 404 ולא 403 בכוונה - קיום דוח שמור של חברה אחרת אסור לדלוף (אותו
        # דפוס בדיוק כמו download_export/assert_document_access).
        raise HTTPException(status_code=404, detail="Saved report not found")
    return _saved_report_out(row)


@router.delete("/admin/reports/saved/{report_id}", response_model=SavedReportDeleteOut)
def delete_saved_report(report_id: str, current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                        db: Session = Depends(get_db)):
    row = db.query(SavedReport).filter(SavedReport.report_id == report_id).first()
    if row is None or row.company_id != current_user.company_id:
        # שורה של חברה אחרת - 404, לא 403 (לא לדלוף קיום).
        raise HTTPException(status_code=404, detail="Saved report not found")
    if row.owner_user_id != current_user.user_id:
        # אותה חברה, לא הבעלים - 403 מפורש (יש קיום, אין הרשאה).
        raise HTTPException(status_code=403, detail="Only the owner may delete this saved report")

    record_audit_event(db, "SavedReport", row.report_id, "DELETE", current_user.user_id,
                       before={"name": row.name, "report_type": row.report_type})
    db.delete(row)
    db.commit()
    return {"deleted": True, "report_id": report_id}
