from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.types import utcnow
from backend.app.models import (
    Employee, Grant, Company, Trustee, User, UserRole,
    Document, DocumentStatus, DOCUMENT_TEMPLATE_TYPES, generate_uuid,
)
from backend.app.schemas import GenerateDocumentRequest, DocumentOut
from backend.app.services.audit import record_audit_event
from backend.app.services.documents import (
    TEMPLATE_BUILDERS, MissingDocumentDataError, DocumentRenderingError, DOCUMENT_STORE_DIR,
)
from backend.app.services.document_access import assert_document_access
from backend.app.services.document_status import (
    assert_is_current_version, assert_transition_allowed, deadline_for, expire_due, expire_if_due,
)
from backend.app.auth import require_roles
from backend.app.api.exercise_requests import _company_id_of_grant

router = APIRouter()


# ===================================================================
# מסמכים ואישור קבלה פנימי (v0.9.0 שלבים 1-2)
# *** לא חתימה - ראו הערת models.py.Document ***
#
# כל endpoint שמחזיר/מוריד/משנה מסמך קורא ל-assert_document_access **ראשון**.
# זו הנקודה שכבר נכשלה 3 פעמים בעבר (QA_TESTBOOK.md P2) - פונקציה אחת, לא
# בדיקה מועתקת. שאילתות *רשימה* מסוננות ב-SQL לפי היקף מ-current_user בלבד,
# בלי שום פרמטר שהלקוח יכול לדרוס.
# ===================================================================

def _documents_out(db: Session, documents: List[Document]) -> List[DocumentOut]:
    """הרכבת תצוגת המסמך: שם העובד ותאריך המענק יושבים בטבלאות אחרות, ובלעדיהם
    השורה בפורטל היא UUID בלבד. שאילתה אחת לכל טבלה ולא אחת לכל מסמך - ספריית
    המסמכים של חברה גדולה לא תייצר N+1."""
    if not documents:
        return []
    # ההפקעה יושבת כאן ולא בכל endpoint בנפרד: זו נקודת החנק היחידה שכל
    # נתיבי הקריאה (אדמין/עובד/נאמן, רשימה ופריט) עוברים דרכה. הוספתה לכל
    # endpoint לחוד היא בדיוק P3 - ולידציה שקיימת בנתיב אחד וחסרה במקביל לו.
    expire_due(db, documents)
    employees = {
        e.employee_id: e for e in db.query(Employee)
        .filter(Employee.employee_id.in_({d.employee_id for d in documents})).all()
    }
    grants = {
        g.grant_id: g for g in db.query(Grant)
        .filter(Grant.grant_id.in_({d.grant_id for d in documents})).all()
    }
    out = []
    for d in documents:
        employee = employees.get(d.employee_id)
        grant = grants.get(d.grant_id)
        out.append(DocumentOut(
            document_id=d.document_id, template_type=d.template_type, grant_id=d.grant_id,
            status=d.status.value, version=d.version, is_latest=d.is_latest,
            file_sha256=d.file_sha256, generated_at=d.generated_at,
            sent_at=d.sent_at, expires_at=d.expires_at, acknowledged_at=d.acknowledged_at,
            # None ולא "" - ראו ההערה ב-DocumentOut. שורה יתומה היא באג נתונים
            # שה-UI צריך להראות ככזה, לא שם ריק שנראה תקין.
            employee_name=f"{employee.first_name} {employee.last_name}" if employee else None,
            grant_date=grant.grant_date if grant else None,
        ))
    return out


def _document_out(db: Session, document: Document) -> DocumentOut:
    return _documents_out(db, [document])[0]


def _load_document_or_404(db: Session, document_id: str, current_user: User) -> Document:
    document = db.query(Document).filter(Document.document_id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    assert_document_access(document, current_user)
    # ההפקעה קודמת לכל פעולה, ולכן אישור של מסמך שפג תוקפו נחסם ב-409 של
    # מכונת המצבים ("EXPIRED הוא סופי") ולא נופל בין הכיסאות.
    return expire_if_due(db, document)


def _transition_document(db: Session, document: Document, target: DocumentStatus,
                         current_user: User, action: str) -> Document:
    """מעבר מצב יחיד + audit, בטרנזקציה אחת. הוולידציה קודמת לכתיבה - מסמך
    שכבר אושר לא מקבל אפקט שני (P5), וגרסה מיושנת לא עוברת מצב בכלל."""
    assert_is_current_version(document.is_latest, target)
    assert_transition_allowed(document.status, target)
    before = document.status.value
    document.status = target
    now = utcnow()
    if target == DocumentStatus.SENT:
        document.sent_at = now
        document.expires_at = deadline_for(now)
    elif target == DocumentStatus.ACKNOWLEDGED:
        document.acknowledged_at = now
        document.acknowledged_by_user_id = current_user.user_id
    record_audit_event(db, "Document", document.document_id, action, current_user.user_id,
                       before={"status": before}, after={"status": target.value})
    db.commit()
    db.refresh(document)
    return document


def _download_document_response(db: Session, document: Document, current_user: User):
    full_path = DOCUMENT_STORE_DIR / document.file_path
    if not full_path.exists():
        raise HTTPException(status_code=500, detail="Document file is missing from storage")
    record_audit_event(db, "Document", document.document_id, "DOWNLOADED", current_user.user_id, after={})
    db.commit()
    return FileResponse(str(full_path), media_type="application/pdf",
                        filename=f"{document.template_type}_{document.grant_id}_v{document.version}.pdf")


# --- ADMIN ---------------------------------------------------------

@router.post("/admin/documents", response_model=DocumentOut)
def generate_document(payload: GenerateDocumentRequest,
                      current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                      db: Session = Depends(get_db)):
    if payload.template_type not in DOCUMENT_TEMPLATE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported template_type: {payload.template_type}")

    grant = db.query(Grant).filter(Grant.grant_id == payload.grant_id).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")

    company_id = _company_id_of_grant(db, grant)
    if company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot generate a document for a grant outside your company")

    employee = db.query(Employee).filter(Employee.employee_id == grant.employee_id).first()
    company = db.query(Company).filter(Company.company_id == company_id).first()
    trustee = db.query(Trustee).filter(Trustee.trustee_id == grant.trustee_id).first() if grant.trustee_id else None

    # גרסה: אם כבר קיים מסמך "אחרון" מאותו סוג לאותו מענק, הוא מפסיק להיות
    # is_latest (לא נמחק, לא נדרס) - החלטת התכנון המפורשת של v0.9.0.
    previous_latest = (
        db.query(Document)
        .filter(Document.grant_id == grant.grant_id, Document.template_type == payload.template_type,
                Document.is_latest == True)  # noqa: E712
        .first()
    )
    next_version = (previous_latest.version + 1) if previous_latest else 1
    document_id = generate_uuid()

    try:
        builder = TEMPLATE_BUILDERS[payload.template_type]
        relative_path, file_hash = builder(grant, employee, company, trustee, document_id, next_version)
    except MissingDocumentDataError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except DocumentRenderingError as e:
        # כשל תשתיתי (למשל אין גופן יוניקוד) - 500, לא 409: זו לא בעיה בנתוני
        # המענק ואין שום דבר שהאדמין יכול לתקן בצד שלו.
        raise HTTPException(status_code=500, detail=str(e))

    if previous_latest:
        previous_latest.is_latest = False

    doc = Document(
        document_id=document_id, template_type=payload.template_type, grant_id=grant.grant_id,
        company_id=company_id, employee_id=grant.employee_id, trustee_id=grant.trustee_id,
        status=DocumentStatus.DRAFT, version=next_version, is_latest=True,
        file_path=relative_path, file_sha256=file_hash, created_by_user_id=current_user.user_id,
    )
    db.add(doc)
    record_audit_event(db, "Document", document_id, "GENERATED", current_user.user_id,
                       after={"template_type": payload.template_type, "grant_id": grant.grant_id,
                             "version": next_version, "file_sha256": file_hash})
    db.commit()
    db.refresh(doc)
    return _document_out(db, doc)


@router.get("/admin/documents", response_model=List[DocumentOut])
def list_company_documents(status: Optional[str] = None, template_type: Optional[str] = None,
                           current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                           db: Session = Depends(get_db)):
    """ספריית המסמכים של החברה. ההיקף נקבע מ-current_user.company_id בלבד -
    אין פרמטר company_id שהלקוח יכול לדרוס."""
    query = db.query(Document).filter(Document.company_id == current_user.company_id)
    if status:
        if status not in {s.value for s in DocumentStatus}:
            raise HTTPException(status_code=400, detail=f"Unknown status: {status}")
        query = query.filter(Document.status == DocumentStatus(status))
    if template_type:
        query = query.filter(Document.template_type == template_type)
    return _documents_out(db, query.order_by(Document.generated_at.desc()).all())


@router.post("/admin/documents/{document_id}/send", response_model=DocumentOut)
def send_document_for_acknowledgment(document_id: str,
                                    current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                                    db: Session = Depends(get_db)):
    document = _load_document_or_404(db, document_id, current_user)
    if not document.is_latest:
        raise HTTPException(status_code=409,
                            detail="This is a superseded version - send the latest version instead")
    document = _transition_document(db, document, DocumentStatus.SENT, current_user, "SENT_FOR_ACKNOWLEDGMENT")
    return _document_out(db, document)


@router.get("/admin/documents/{document_id}/download")
def download_document_admin(document_id: str,
                            current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                            db: Session = Depends(get_db)):
    document = _load_document_or_404(db, document_id, current_user)
    return _download_document_response(db, document, current_user)


# --- EMPLOYEE ------------------------------------------------------

@router.get("/employee/documents", response_model=List[DocumentOut])
def list_my_documents(current_user: User = Depends(require_roles(UserRole.EMPLOYEE)),
                      db: Session = Depends(get_db)):
    """רק המסמכים של העובד המחובר, ורק כאלה שנשלחו לו בפועל - טיוטה שהאדמין
    עוד לא שלח אינה אמורה להיות גלויה לעובד."""
    return _documents_out(db, (
        db.query(Document)
        .filter(Document.employee_id == current_user.employee_id,
                Document.status != DocumentStatus.DRAFT)
        .order_by(Document.generated_at.desc())
    ).all())


@router.get("/employee/documents/{document_id}/download")
def download_document_employee(document_id: str,
                               current_user: User = Depends(require_roles(UserRole.EMPLOYEE)),
                               db: Session = Depends(get_db)):
    document = _load_document_or_404(db, document_id, current_user)
    if document.status == DocumentStatus.DRAFT:
        raise HTTPException(status_code=403, detail="This document has not been sent to you yet")
    return _download_document_response(db, document, current_user)


@router.post("/employee/documents/{document_id}/acknowledge", response_model=DocumentOut)
def acknowledge_document_employee(document_id: str,
                                  current_user: User = Depends(require_roles(UserRole.EMPLOYEE)),
                                  db: Session = Depends(get_db)):
    """*** אישור קבלה פנימי, לא חתימה משפטית *** - ראו models.py.Document."""
    document = _load_document_or_404(db, document_id, current_user)
    document = _transition_document(db, document, DocumentStatus.ACKNOWLEDGED, current_user, "ACKNOWLEDGED")
    return _document_out(db, document)


@router.post("/employee/documents/{document_id}/decline", response_model=DocumentOut)
def decline_document_employee(document_id: str,
                              current_user: User = Depends(require_roles(UserRole.EMPLOYEE)),
                              db: Session = Depends(get_db)):
    document = _load_document_or_404(db, document_id, current_user)
    document = _transition_document(db, document, DocumentStatus.DECLINED, current_user, "DECLINED")
    return _document_out(db, document)


# --- TRUSTEE -------------------------------------------------------

@router.get("/trustee/documents/pending", response_model=List[DocumentOut])
def list_pending_documents_trustee(current_user: User = Depends(require_roles(UserRole.TRUSTEE)),
                                   db: Session = Depends(get_db)):
    """תור האישורים הממתינים של הנאמן - רק מסמכים על מענקים שהוא הנאמן שלהם,
    ורק כאלה שממתינים בפועל.

    is_latest נכנס לסינון כאן ולא רק בתצוגה: זה *תור פעולה*, ומסמך שהחברה
    כבר החליפה בגרסה חדשה אינו פעולה שממתינה - השרת דוחה אישור עליו ממילא
    (assert_is_current_version). בפורטל העובד ההתנהגות שונה בכוונה: שם הרשימה
    היא היסטוריה, ולכן גרסה מיושנת נשארת מוצגת ומסומנת ככזו."""
    return _documents_out(db, (
        db.query(Document)
        .filter(Document.trustee_id == current_user.trustee_id,
                Document.status == DocumentStatus.SENT,
                Document.is_latest == True)  # noqa: E712
        .order_by(Document.generated_at.desc())
    ).all())


@router.get("/trustee/documents/{document_id}/download")
def download_document_trustee(document_id: str,
                              current_user: User = Depends(require_roles(UserRole.TRUSTEE)),
                              db: Session = Depends(get_db)):
    document = _load_document_or_404(db, document_id, current_user)
    if document.status == DocumentStatus.DRAFT:
        raise HTTPException(status_code=403, detail="This document has not been sent for acknowledgment yet")
    return _download_document_response(db, document, current_user)


@router.post("/trustee/documents/{document_id}/acknowledge", response_model=DocumentOut)
def acknowledge_document_trustee(document_id: str,
                                 current_user: User = Depends(require_roles(UserRole.TRUSTEE)),
                                 db: Session = Depends(get_db)):
    """*** אישור קבלה פנימי, לא חתימה משפטית *** - ראו models.py.Document."""
    document = _load_document_or_404(db, document_id, current_user)
    document = _transition_document(db, document, DocumentStatus.ACKNOWLEDGED, current_user, "ACKNOWLEDGED")
    return _document_out(db, document)


@router.post("/trustee/documents/{document_id}/decline", response_model=DocumentOut)
def decline_document_trustee(document_id: str,
                             current_user: User = Depends(require_roles(UserRole.TRUSTEE)),
                             db: Session = Depends(get_db)):
    document = _load_document_or_404(db, document_id, current_user)
    document = _transition_document(db, document, DocumentStatus.DECLINED, current_user, "DECLINED")
    return _document_out(db, document)
