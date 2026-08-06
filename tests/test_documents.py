"""v0.9.0 שלב 1 - מסמכי PDF (כתב הענקה בלבד) ואישור-קבלה פנימי, admin-only.

*** לא חתימה - ראו models.py.Document. שום assertion כאן לא בודק "חתימה". ***
מיפוי ל-docs/qa/v0.9.0.md.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from pypdf import PdfReader

from backend.app.auth import hash_password
from backend.app.models import (
    Company, Document, DocumentStatus, Employee, EmployeeStatus, OptionPool,
    Trustee, User, UserRole, UserSession,
)
import backend.app.services.documents as documents_module
import backend.app.api.routes as routes_module

API = "/api/v1"


@pytest.fixture(autouse=True)
def isolated_document_store(tmp_path, monkeypatch):
    """מפנה את קבצי ה-PDF לתיקיית tmp_path של הבדיקה, לא ל-document_store/
    האמיתי בשורש הפרויקט - אחרת כל הרצת בדיקות משאירה קבצים אמיתיים מאחור.
    שני המודולים (documents.py ו-routes.py) מחזיקים כל אחד binding נפרד
    לקבוע הזה (import ישיר, לא import module) - שניהם צריכים תיקון."""
    test_store = tmp_path / "document_store"
    monkeypatch.setattr(documents_module, "DOCUMENT_STORE_DIR", test_store)
    monkeypatch.setattr(routes_module, "DOCUMENT_STORE_DIR", test_store)
    return test_store


def _months_ago(months: int) -> date:
    total = date.today().month - 1 - months
    return date(date.today().year + total // 12, total % 12 + 1, min(date.today().day, 28))


def _token(db, user):
    token = f"tok-{user.user_id}"
    db.add(UserSession(token=token, user_id=user.user_id,
                       expires_at=datetime.utcnow() + timedelta(hours=1)))
    db.flush()
    return {"Authorization": f"Bearer {token}"}


def _user(db, user_id, role, **ids):
    pw_hash, salt = hash_password("Demo1234!")
    u = User(user_id=user_id, username=f"{user_id.lower()}@test.example",
             password_hash=pw_hash, password_salt=salt, role=role, is_active=True, **ids)
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def world(db_session):
    db = db_session
    db.add_all([
        Company(company_id="C-A", name="Alpha", country_code="IL"),
        Company(company_id="C-B", name="Beta", country_code="IL"),
    ])
    db.flush()
    db.add(OptionPool(pool_id="P-A", company_id="C-A", total_shares=100000.0,
                      allocated_shares=0.0, unallocated_shares=100000.0))
    db.add(Trustee(trustee_id="T-1", company_id="C-A", name="Trustee Ltd", registration_number="1"))
    db.add(Employee(employee_id="E-1", company_id="C-A", first_name="Yossi", last_name="Cohen",
                    email="e1@alpha.example", country_code="IL", status=EmployeeStatus.ACTIVE,
                    hire_date=date(2020, 1, 1), birth_date=date(1990, 1, 1),
                    national_id="123456789"))
    db.flush()

    admin_a = _user(db, "U-ADMIN-A", UserRole.COMPANY_ADMIN, company_id="C-A")
    admin_b = _user(db, "U-ADMIN-B", UserRole.COMPANY_ADMIN, company_id="C-B")
    trustee_a = _user(db, "U-TRUSTEE-A", UserRole.TRUSTEE, trustee_id="T-1")
    employee_a = _user(db, "U-EMPLOYEE-A", UserRole.EMPLOYEE, employee_id="E-1")
    from types import SimpleNamespace
    return SimpleNamespace(db=db, admin_a=_token(db, admin_a), admin_b=_token(db, admin_b),
                           trustee_a=_token(db, trustee_a), employee_a=_token(db, employee_a))


@pytest.fixture
def grant_id(client, world):
    r = client.post(f"{API}/admin/grants", headers=world.admin_a, json={
        "employee_id": "E-1", "pool_id": "P-A", "trustee_id": "T-1",
        "grant_type": "IL_102_CAPITAL_GAINS", "total_options": 4800.0,
        "exercise_price": 2.5, "grant_date": str(_months_ago(20)),
        "cliff_months": 12, "total_months": 48,
    })
    assert r.status_code == 200, r.text
    return r.json()["grant_id"]


# ===================================================================
# QA-090-01..04: יצירת מסמך
# ===================================================================

def test_generate_grant_letter_succeeds_and_writes_a_real_file(client, world, grant_id):
    response = client.post(f"{API}/admin/documents", headers=world.admin_a,
                           json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["template_type"] == "GRANT_LETTER"
    assert body["status"] == "DRAFT"
    assert body["version"] == 1
    assert body["is_latest"] is True

    doc = world.db.query(Document).filter(Document.document_id == body["document_id"]).first()
    full_path = documents_module.DOCUMENT_STORE_DIR / doc.file_path
    assert full_path.exists()
    assert full_path.stat().st_size > 0


def test_unsupported_template_type_is_rejected(client, world, grant_id):
    response = client.post(f"{API}/admin/documents", headers=world.admin_a,
                           json={"grant_id": grant_id, "template_type": "NOT_A_REAL_TYPE"})
    assert response.status_code == 400


def test_not_yet_implemented_template_type_is_rejected_not_silently_generated(client, world, grant_id):
    """SECTION_102_APPENDIX הוא סוג חוקי (ב-DOCUMENT_TEMPLATE_TYPES) אבל טרם
    מומש - שלב 2. חייב 400 מפורש, לא ניסיון ליצור PDF ריק/שגוי."""
    response = client.post(f"{API}/admin/documents", headers=world.admin_a,
                           json={"grant_id": grant_id, "template_type": "SECTION_102_APPENDIX"})
    assert response.status_code == 400


def test_unknown_grant_returns_404(client, world):
    response = client.post(f"{API}/admin/documents", headers=world.admin_a,
                           json={"grant_id": "does-not-exist", "template_type": "GRANT_LETTER"})
    assert response.status_code == 404


def test_grant_without_vesting_schedule_fails_loudly_not_partial_pdf(client, world):
    """החלטת התכנון: אין לוח הבשלה => כשל מפורש (409), לא PDF בלי סעיף הבשלה."""
    r = client.post(f"{API}/admin/grants", headers=world.admin_a, json={
        "employee_id": "E-1", "pool_id": "P-A", "grant_type": "IL_102_CAPITAL_GAINS",
        "total_options": 100.0, "exercise_price": 1.0, "grant_date": str(_months_ago(5)),
    })
    grant_no_schedule_id = r.json()["grant_id"]
    from backend.app.models import VestingSchedule
    sched = world.db.query(VestingSchedule).filter(
        VestingSchedule.grant_id == grant_no_schedule_id).first()
    world.db.delete(sched)
    world.db.flush()

    response = client.post(f"{API}/admin/documents", headers=world.admin_a,
                           json={"grant_id": grant_no_schedule_id, "template_type": "GRANT_LETTER"})
    assert response.status_code == 409
    assert "vesting schedule" in response.json()["detail"].lower()


# ===================================================================
# QA-090-05..06: גרסאות - שינוי לא דורס, יוצר גרסה חדשה
# ===================================================================

def test_regenerating_creates_a_new_version_not_an_overwrite(client, world, grant_id):
    r1 = client.post(f"{API}/admin/documents", headers=world.admin_a,
                     json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    doc1_id = r1.json()["document_id"]
    assert r1.json()["version"] == 1
    assert r1.json()["is_latest"] is True

    r2 = client.post(f"{API}/admin/documents", headers=world.admin_a,
                     json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    assert r2.json()["version"] == 2
    assert r2.json()["is_latest"] is True

    world.db.refresh(world.db.query(Document).filter(Document.document_id == doc1_id).first())
    doc1 = world.db.query(Document).filter(Document.document_id == doc1_id).first()
    assert doc1.is_latest is False
    # הישנה לא נמחקה ולא נדרסה - הקובץ שלה עדיין קיים בנפרד.
    assert (documents_module.DOCUMENT_STORE_DIR / doc1.file_path).exists()


# ===================================================================
# QA-090-07..10: IDOR - אותו דפוס שכבר נכשל 3 פעמים בעבר במערכת הזו
# ===================================================================

def test_admin_cannot_generate_document_for_another_companys_grant(client, world, grant_id):
    response = client.post(f"{API}/admin/documents", headers=world.admin_b,
                           json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    assert response.status_code == 403


def test_admin_cannot_download_another_companys_document(client, world, grant_id):
    gen = client.post(f"{API}/admin/documents", headers=world.admin_a,
                      json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    document_id = gen.json()["document_id"]

    response = client.get(f"{API}/admin/documents/{document_id}/download", headers=world.admin_b)
    assert response.status_code == 403


def test_unknown_document_id_download_returns_404(client, world):
    response = client.get(f"{API}/admin/documents/does-not-exist/download", headers=world.admin_a)
    assert response.status_code == 404


@pytest.mark.parametrize("role_header", ["trustee_a", "employee_a"])
def test_non_admin_roles_cannot_generate_or_download_documents_yet(client, world, grant_id, role_header):
    """שלב 1: admin-only. עובד/נאמן מקבלים endpoints משלהם בשלב 2/3, לא עכשיו."""
    headers = getattr(world, role_header)
    gen = client.post(f"{API}/admin/documents", headers=headers,
                      json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    assert gen.status_code == 403

    admin_gen = client.post(f"{API}/admin/documents", headers=world.admin_a,
                            json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    document_id = admin_gen.json()["document_id"]
    download = client.get(f"{API}/admin/documents/{document_id}/download", headers=headers)
    assert download.status_code == 403


def test_successful_download_returns_pdf_and_records_audit(client, world, grant_id):
    gen = client.post(f"{API}/admin/documents", headers=world.admin_a,
                      json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    document_id = gen.json()["document_id"]

    response = client.get(f"{API}/admin/documents/{document_id}/download", headers=world.admin_a)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert len(response.content) > 0

    from backend.app.models import AuditLog
    events = world.db.query(AuditLog).filter(AuditLog.entity_id == document_id).all()
    actions = {e.action for e in events}
    assert "GENERATED" in actions
    assert "DOWNLOADED" in actions


# ===================================================================
# QA-090-11: תיקון באג אמיתי - שם עברי חייב להידפס קריא ובכיוון נכון,
# לא כריבועים (אין glyphs) ולא הפוך (אין BIDI). נמצא באימות ידני מול
# G-2021-001 בסביבת סנדבוקס - שני באגים נפרדים, שני תיקונים נפרדים
# (רישום גופן יוניקוד + python-bidi), שניהם צריכים בדיקת רגרסיה.
# ===================================================================

def test_hebrew_employee_name_renders_correctly_ordered_in_the_pdf(client, world, grant_id):
    hebrew_first, hebrew_last = "ישראל", "ישראלי"
    employee = world.db.query(Employee).filter(Employee.employee_id == "E-1").first()
    employee.first_name, employee.last_name = hebrew_first, hebrew_last
    world.db.flush()

    gen = client.post(f"{API}/admin/documents", headers=world.admin_a,
                      json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    assert gen.status_code == 200, gen.text
    document_id = gen.json()["document_id"]

    download = client.get(f"{API}/admin/documents/{document_id}/download", headers=world.admin_a)
    assert download.status_code == 200

    import io
    reader = PdfReader(io.BytesIO(download.content))
    text = "\n".join(page.extract_text() for page in reader.pages)

    # לא ריבועי "אין glyph" (הבאג הראשון: פונט ברירת המחדל של ReportLab).
    assert "�" not in text and "■" not in text
    # לא הפוך (הבאג השני: אין BIDI) - השם חייב להופיע בסדר הקריאה הטבעי שלו,
    # לא מוחלף. "ילארשי" הוא מה שהיה מודפס לפני התיקון (get_display הפוך).
    assert f"{hebrew_first} {hebrew_last}" in text
    assert hebrew_first[::-1] not in text
