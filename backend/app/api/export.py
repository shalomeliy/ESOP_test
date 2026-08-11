import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import User, UserRole, DataTransferRun, DataTransferDirection, DataTransferStatus
from backend.app.schemas import DataTransferRunOut, ImportDryRunReportOut, ImportRowErrorOut
from backend.app.services.audit import record_audit_event
from backend.app.services.export import (
    EXPORT_SCHEMA_VERSION, EXPORT_STORE_DIR, ExportTooLargeError, assert_export_within_size_limit,
    read_export_json, render_bundle_as_csv_zip, run_export, write_export_json,
)
from backend.app.services.import_ import (
    ImportFileTooLargeError, ImportJsonTooDeepError, ImportSchemaVersionMismatch,
    ImportTooManyRowsError, InvalidImportBundleError, MAX_IMPORT_FILE_BYTES,
    dry_run, parse_and_validate_bundle_shape,
)
from backend.app.auth import require_roles

router = APIRouter()

_DOWNLOAD_FORMATS = {"json", "csv"}


# ===================================================================
# ייצוא נתונים (v0.9.1 שלב ב) - היקף מלא (PLAN.md §8 step 4).
#
# בדיוק כמו מסמכים (documents.py): הורדה אף פעם לא קובץ סטטי, רק endpoint
# מאומת שבודק company_id על שורת ה-DataTransferRun עצמה - לא על נתיב שהלקוח
# שולט בו. נשמר קובץ JSON יחיד לכל ריצה; CSV נגזר ממנו בזמן ההורדה ולא
# נשמר בנפרד - כדי שלא יהיו שני מקורות אמת לאותם נתונים (ראו
# render_bundle_as_csv_zip).
# ===================================================================

@router.post("/admin/export", response_model=DataTransferRunOut)
def export_company_data(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                        db: Session = Depends(get_db)):
    # נכשל *לפני* run_export, לא באמצעו: v0.9.1 סינכרונית בכוונה (אין תור-
    # עבודה), אז חברה גדולה מספיק הייתה נתקעת ב-timeout שקט בלי הבדיקה הזו.
    try:
        assert_export_within_size_limit(db, current_user.company_id)
    except ExportTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))

    bundle = run_export(db, current_user.company_id)
    row_counts = {name: len(rows) for name, rows in bundle["tables"].items()}
    rows_total = sum(row_counts.values())

    run = DataTransferRun(
        direction=DataTransferDirection.EXPORT,
        source_company_id=current_user.company_id,
        initiated_by_user_id=current_user.user_id,
        export_schema_version=EXPORT_SCHEMA_VERSION,
        rows_attempted=rows_total, rows_succeeded=rows_total, rows_failed=0,
        status=DataTransferStatus.SUCCESS,
    )
    db.add(run)
    db.flush()

    run.file_path = write_export_json(bundle, run.run_id)
    record_audit_event(db, "DataTransferRun", run.run_id, "EXPORTED", current_user.user_id,
                       after={"direction": "EXPORT", "row_counts": row_counts,
                             "contains_demo_tax_data": bundle["contains_demo_tax_data"]})
    db.commit()
    db.refresh(run)
    return run


@router.get("/admin/export/{run_id}/download")
def download_export(run_id: str, format: str = "json",
                    current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                    db: Session = Depends(get_db)):
    if format not in _DOWNLOAD_FORMATS:
        raise HTTPException(status_code=400, detail=f"format must be one of {sorted(_DOWNLOAD_FORMATS)}")

    run = db.query(DataTransferRun).filter(DataTransferRun.run_id == run_id).first()
    # אותו דפוס בדיוק כמו assert_document_access: 404 כשה-run לא קיים בכלל
    # (או לא ייצוא), 403 כשהוא קיים אבל שייך לחברה אחרת.
    if not run or run.direction != DataTransferDirection.EXPORT:
        raise HTTPException(status_code=404, detail="Export not found")
    if run.source_company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="This export does not belong to your company")
    if not run.file_path:
        raise HTTPException(status_code=500, detail="Export file is missing from storage")

    full_path = EXPORT_STORE_DIR / run.file_path
    if not full_path.exists():
        raise HTTPException(status_code=500, detail="Export file is missing from storage")

    record_audit_event(db, "DataTransferRun", run.run_id, "DOWNLOADED", current_user.user_id,
                       after={"format": format})
    db.commit()

    if format == "json":
        return FileResponse(str(full_path), media_type="application/json",
                            filename=f"export_{run.run_id}.json")

    zip_bytes = render_bundle_as_csv_zip(read_export_json(run.file_path))
    return StreamingResponse(
        io.BytesIO(zip_bytes), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="export_{run.run_id}.csv.zip"'},
    )


# ===================================================================
# ייבוא - דריי-ראן בלבד (PLAN.md §8 step 6). לא כותב שורת דומיין אחת; רק
# מאמת ושומר את החבילה הגולמית (ל-commit עתידי, task #7/8, שמריץ dry_run
# מחדש מול מה שבאמת נשמר - לא סומך על הדוח הישן שכבר יכול היה להתיישן).
# ===================================================================

@router.post("/admin/import/dry-run", response_model=ImportDryRunReportOut)
def import_dry_run(file: UploadFile = File(...),
                   current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                   db: Session = Depends(get_db)):
    # נקרא עד תקרה+1 בלבד - לעולם לא בופר בלתי-מוגבל בזיכרון, גם אם הלקוח
    # מנסה להעלות קובץ ענק בכוונה (decision 9: "נדחה ברמת הבקשה, לא אחרי
    # buffering מלא").
    raw = file.file.read(MAX_IMPORT_FILE_BYTES + 1)

    try:
        bundle = parse_and_validate_bundle_shape(raw)
    except ImportFileTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except ImportTooManyRowsError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except ImportSchemaVersionMismatch as e:
        raise HTTPException(status_code=422, detail=str(e))
    except (ImportJsonTooDeepError, InvalidImportBundleError) as e:
        raise HTTPException(status_code=422, detail=str(e))

    report = dry_run(db, bundle, current_user.company_id)

    run = DataTransferRun(
        direction=DataTransferDirection.IMPORT_DRY_RUN,
        target_company_id=current_user.company_id,
        initiated_by_user_id=current_user.user_id,
        export_schema_version=bundle["export_schema_version"],
        rows_attempted=report.rows_attempted,
        rows_succeeded=report.rows_new + report.rows_skipped_existing,
        rows_failed=report.rows_failed,
        status=DataTransferStatus.SUCCESS if report.valid else DataTransferStatus.FAILED,
    )
    db.add(run)
    db.flush()
    # שומר את החבילה הגולמית (לא רק את הדוח) - commit (task #7) קורא אותה
    # מחדש דרך dry_run_id, ומריץ dry_run עליה שוב לפני שכותב, לא רק מעתיק
    # את המסקנה הישנה.
    run.file_path = write_export_json(bundle, run.run_id)
    record_audit_event(db, "DataTransferRun", run.run_id, "IMPORT_DRY_RUN", current_user.user_id,
                       after={"valid": report.valid, "rows_new": report.rows_new,
                             "rows_skipped_existing": report.rows_skipped_existing,
                             "rows_failed": report.rows_failed})
    db.commit()
    db.refresh(run)

    return ImportDryRunReportOut(
        run_id=run.run_id, status=run.status.value, rows_attempted=report.rows_attempted,
        rows_new=report.rows_new, rows_skipped_existing=report.rows_skipped_existing,
        rows_failed=report.rows_failed,
        errors=[ImportRowErrorOut(table=o.table, index=o.index, row_id=o.row_id, error=o.error)
               for o in report.errors],
    )
