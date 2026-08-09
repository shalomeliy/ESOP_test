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


def test_section_102_appendix_generates_for_an_il_102_grant(client, world, grant_id):
    """שלב 2: הסוג הזה החזיר 400 ("טרם מומש") בשלב 1 - הבדיקה ההיא הוחלפה
    בכוונה, כי ההתנהגות שהיא נעלה השתנתה במתכוון."""
    response = client.post(f"{API}/admin/documents", headers=world.admin_a,
                           json={"grant_id": grant_id, "template_type": "SECTION_102_APPENDIX"})
    assert response.status_code == 200, response.text
    assert response.json()["template_type"] == "SECTION_102_APPENDIX"


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


# ===================================================================
# שלב 2 — QA-090-20..25: שתי התבניות הנוספות
# ===================================================================

def _us_grant(client, world):
    world.db.add(Employee(employee_id="E-US", company_id="C-A", first_name="Dana", last_name="Levi",
                          email="eus@alpha.example", country_code="US", status=EmployeeStatus.ACTIVE,
                          hire_date=date(2020, 1, 1), birth_date=date(1990, 1, 1)))
    world.db.flush()
    r = client.post(f"{API}/admin/grants", headers=world.admin_a, json={
        "employee_id": "E-US", "pool_id": "P-A", "trustee_id": "T-1", "grant_type": "US_ISO",
        "total_options": 100.0, "exercise_price": 1.0, "grant_date": str(_months_ago(10)),
    })
    assert r.status_code == 200, r.text
    return r.json()["grant_id"]


def test_section_102_appendix_is_rejected_for_a_us_grant(client, world):
    """נספח 102 לא חל על מסלול אמריקאי. זו לא הכרעת מס חדשה - אי-תחולה."""
    us_grant_id = _us_grant(client, world)
    response = client.post(f"{API}/admin/documents", headers=world.admin_a,
                           json={"grant_id": us_grant_id, "template_type": "SECTION_102_APPENDIX"})
    assert response.status_code == 409
    assert "Section 102" in response.json()["detail"]


def test_section_102_appendix_is_rejected_without_a_trustee(client, world):
    r = client.post(f"{API}/admin/grants", headers=world.admin_a, json={
        "employee_id": "E-1", "pool_id": "P-A", "grant_type": "IL_102_CAPITAL_GAINS",
        "total_options": 100.0, "exercise_price": 1.0, "grant_date": str(_months_ago(10)),
    })
    no_trustee_grant = r.json()["grant_id"]
    response = client.post(f"{API}/admin/documents", headers=world.admin_a,
                           json={"grant_id": no_trustee_grant, "template_type": "SECTION_102_APPENDIX"})
    assert response.status_code == 409
    assert "trustee" in response.json()["detail"].lower()


def test_section_102_appendix_pdf_is_marked_as_a_demo_template(client, world, grant_id):
    """ההחלטה המפורשת בתכנון: התבנית מסומנת כדמו *בגוף ה-PDF*, לא רק בהערת
    קוד - אחרת מי שמחזיק את הקובץ ביד לא יכול לדעת שאין לו תוקף משפטי."""
    gen = client.post(f"{API}/admin/documents", headers=world.admin_a,
                      json={"grant_id": grant_id, "template_type": "SECTION_102_APPENDIX"})
    document_id = gen.json()["document_id"]
    download = client.get(f"{API}/admin/documents/{document_id}/download", headers=world.admin_a)

    import io
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(download.content)).pages)
    assert "DEMO TEMPLATE" in text
    assert "not constitute a legally binding agreement or signature" in text


def test_trustee_deposit_confirmation_requires_an_actual_deposit_date(client, world, grant_id):
    """אישור על הפקדה שלא נרשמה הוא מסמך מטעה - נכשל בגלוי."""
    response = client.post(f"{API}/admin/documents", headers=world.admin_a,
                           json={"grant_id": grant_id, "template_type": "TRUSTEE_DEPOSIT_CONFIRMATION"})
    assert response.status_code == 409
    assert "deposit" in response.json()["detail"].lower()


def test_trustee_deposit_confirmation_succeeds_once_the_deposit_is_recorded(client, world, grant_id):
    from backend.app.models import Grant
    grant = world.db.query(Grant).filter(Grant.grant_id == grant_id).first()
    grant.trustee_deposit_date = _months_ago(19)
    world.db.flush()

    response = client.post(f"{API}/admin/documents", headers=world.admin_a,
                           json={"grant_id": grant_id, "template_type": "TRUSTEE_DEPOSIT_CONFIRMATION"})
    assert response.status_code == 200, response.text
    assert response.json()["template_type"] == "TRUSTEE_DEPOSIT_CONFIRMATION"


# ===================================================================
# שלב 2 — QA-090-30..36: מכונת המצבים (P5 - אידמפוטנטיות)
# ===================================================================

@pytest.fixture
def sent_document(client, world, grant_id):
    gen = client.post(f"{API}/admin/documents", headers=world.admin_a,
                      json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    document_id = gen.json()["document_id"]
    sent = client.post(f"{API}/admin/documents/{document_id}/send", headers=world.admin_a)
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "SENT"
    return document_id


def test_draft_can_be_sent(client, world, sent_document):
    doc = world.db.query(Document).filter(Document.document_id == sent_document).first()
    assert doc.status == DocumentStatus.SENT
    assert doc.sent_at is not None


def test_employee_can_acknowledge_a_sent_document(client, world, sent_document):
    response = client.post(f"{API}/employee/documents/{sent_document}/acknowledge",
                           headers=world.employee_a)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ACKNOWLEDGED"

    doc = world.db.query(Document).filter(Document.document_id == sent_document).first()
    assert doc.acknowledged_at is not None
    assert doc.acknowledged_by_user_id == "U-EMPLOYEE-A"


def test_acknowledging_twice_is_rejected_not_applied_twice(client, world, sent_document):
    """P5: אותה פעולה פעמיים לא מייצרת אפקט שני."""
    first = client.post(f"{API}/employee/documents/{sent_document}/acknowledge", headers=world.employee_a)
    assert first.status_code == 200

    second = client.post(f"{API}/employee/documents/{sent_document}/acknowledge", headers=world.employee_a)
    assert second.status_code == 409
    assert "already ACKNOWLEDGED" in second.json()["detail"]


def test_cannot_acknowledge_a_document_that_was_never_sent(client, world, grant_id):
    gen = client.post(f"{API}/admin/documents", headers=world.admin_a,
                      json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    draft_id = gen.json()["document_id"]

    response = client.post(f"{API}/employee/documents/{draft_id}/acknowledge", headers=world.employee_a)
    assert response.status_code == 409
    assert "DRAFT" in response.json()["detail"]


def test_acknowledged_is_terminal_and_cannot_be_declined_afterwards(client, world, sent_document):
    client.post(f"{API}/employee/documents/{sent_document}/acknowledge", headers=world.employee_a)
    response = client.post(f"{API}/employee/documents/{sent_document}/decline", headers=world.employee_a)
    assert response.status_code == 409
    assert "final" in response.json()["detail"].lower()


def test_sending_a_superseded_version_is_rejected(client, world, grant_id):
    first = client.post(f"{API}/admin/documents", headers=world.admin_a,
                        json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    old_id = first.json()["document_id"]
    client.post(f"{API}/admin/documents", headers=world.admin_a,
               json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})

    response = client.post(f"{API}/admin/documents/{old_id}/send", headers=world.admin_a)
    assert response.status_code == 409
    assert "superseded" in response.json()["detail"].lower()


# ===================================================================
# שלב 2 — QA-090-40..46: היקף רשימות והרשאות בשלושת הפורטלים
# ===================================================================

def test_employee_list_hides_drafts_and_shows_only_own_documents(client, world, grant_id, sent_document):
    client.post(f"{API}/admin/documents", headers=world.admin_a,
               json={"grant_id": grant_id, "template_type": "SECTION_102_APPENDIX"})  # נשאר DRAFT

    response = client.get(f"{API}/employee/documents", headers=world.employee_a)
    assert response.status_code == 200
    ids = [d["document_id"] for d in response.json()]
    assert sent_document in ids
    assert all(d["status"] != "DRAFT" for d in response.json())


def test_employee_cannot_download_a_draft_not_yet_sent(client, world, grant_id):
    gen = client.post(f"{API}/admin/documents", headers=world.admin_a,
                      json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    draft_id = gen.json()["document_id"]

    response = client.get(f"{API}/employee/documents/{draft_id}/download", headers=world.employee_a)
    assert response.status_code == 403


def test_trustee_pending_queue_shows_only_own_sent_documents(client, world, sent_document):
    response = client.get(f"{API}/trustee/documents/pending", headers=world.trustee_a)
    assert response.status_code == 200
    ids = [d["document_id"] for d in response.json()]
    assert sent_document in ids
    assert all(d["status"] == "SENT" for d in response.json())


def test_admin_list_is_scoped_to_own_company(client, world, grant_id):
    client.post(f"{API}/admin/documents", headers=world.admin_a,
               json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})

    mine = client.get(f"{API}/admin/documents", headers=world.admin_a)
    assert mine.status_code == 200 and len(mine.json()) >= 1

    other = client.get(f"{API}/admin/documents", headers=world.admin_b)
    assert other.status_code == 200
    assert other.json() == []


def test_admin_list_rejects_an_unknown_status_filter(client, world):
    response = client.get(f"{API}/admin/documents?status=NOT_A_STATUS", headers=world.admin_a)
    assert response.status_code == 400


def test_another_employee_cannot_acknowledge_someone_elses_document(client, world, sent_document):
    """P2/IDOR: מזהה מסמך תקין אצל עובד אחר - חייב 403, לא אישור בשמו."""
    world.db.add(Employee(employee_id="E-2", company_id="C-A", first_name="Other", last_name="Person",
                          email="e2@alpha.example", country_code="IL", status=EmployeeStatus.ACTIVE,
                          hire_date=date(2020, 1, 1)))
    world.db.flush()
    other_employee = _user(world.db, "U-EMPLOYEE-B", UserRole.EMPLOYEE, employee_id="E-2")
    other_headers = _token(world.db, other_employee)

    response = client.post(f"{API}/employee/documents/{sent_document}/acknowledge", headers=other_headers)
    assert response.status_code == 403

    doc = world.db.query(Document).filter(Document.document_id == sent_document).first()
    assert doc.status == DocumentStatus.SENT  # לא השתנה


def test_a_different_trustee_cannot_acknowledge_the_document(client, world, sent_document):
    world.db.add(Trustee(trustee_id="T-2", company_id="C-A", name="Other Trust", registration_number="2"))
    world.db.flush()
    other_trustee = _user(world.db, "U-TRUSTEE-B", UserRole.TRUSTEE, trustee_id="T-2")
    other_headers = _token(world.db, other_trustee)

    response = client.post(f"{API}/trustee/documents/{sent_document}/acknowledge", headers=other_headers)
    assert response.status_code == 403


# --- שלב 3: השדות שהפורטלים צריכים כדי להציג שורה קריאה --------------
# grant_id הוא UUID לכל מענק שנוצר דרך ה-API, ולכן רשימה שמציגה רק אותו אינה
# שמישה. השדות האלה נבדקים על *כל* שלושת התפקידים - אותו DocumentOut מוגש
# בשלושתם, ושדה שנשמט באחד מהם הוא בדיוק דפוס P3 (ולידציה/התנהגות שקיימת
# בנתיב אחד וחסרה בשני).

def test_document_response_carries_the_display_fields_the_portals_need(client, world, grant_id):
    response = client.post(f"{API}/admin/documents", headers=world.admin_a,
                           json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    assert response.status_code == 200
    body = response.json()

    assert body["employee_name"] == "Yossi Cohen"
    assert body["grant_date"] == str(_months_ago(20))
    assert body["sent_at"] is None            # טיוטה - עוד לא נשלחה
    assert body["acknowledged_at"] is None


def test_send_and_acknowledge_populate_their_timestamps_in_the_response(client, world, sent_document):
    listed = client.get(f"{API}/admin/documents", headers=world.admin_a).json()
    row = next(d for d in listed if d["document_id"] == sent_document)
    assert row["sent_at"] is not None
    assert row["acknowledged_at"] is None

    acked = client.post(f"{API}/employee/documents/{sent_document}/acknowledge",
                        headers=world.employee_a)
    assert acked.status_code == 200
    assert acked.json()["acknowledged_at"] is not None


def test_employee_and_trustee_lists_carry_the_same_display_fields(client, world, sent_document):
    employee_row = client.get(f"{API}/employee/documents", headers=world.employee_a).json()[0]
    trustee_row = client.get(f"{API}/trustee/documents/pending", headers=world.trustee_a).json()[0]

    for row in (employee_row, trustee_row):
        assert row["employee_name"] == "Yossi Cohen"
        assert row["grant_date"] == str(_months_ago(20))
        assert row["sent_at"] is not None


# --- סקירת שלב 3: אישור על גרסה מיושנת -------------------------------
# עד שלב 3 is_latest נבדק בשליחה בלבד. אישור ודחייה - בשני התפקידים - לא בדקו
# אותו, כך שעובד יכול היה לאשר כתב הענקה שהחברה כבר החליפה, והמערכת רשמה
# acknowledged_at על נייר שאינו הנייר הנוכחי. זה דפוס P3 בצורתו הנקייה.

@pytest.fixture
def superseded_sent_document(client, world, grant_id, sent_document):
    """מסמך שנשלח, ואז נוצרה לו גרסה חדשה - כלומר SENT אבל is_latest=False."""
    regenerated = client.post(f"{API}/admin/documents", headers=world.admin_a,
                              json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    assert regenerated.status_code == 200, regenerated.text
    assert regenerated.json()["version"] == 2

    stale = world.db.query(Document).filter(Document.document_id == sent_document).first()
    world.db.refresh(stale)
    assert stale.status == DocumentStatus.SENT and stale.is_latest is False
    return sent_document


@pytest.mark.parametrize("role_header,path_prefix", [
    ("employee_a", "employee"),
    ("trustee_a", "trustee"),
])
@pytest.mark.parametrize("decision", ["acknowledge", "decline"])
def test_a_superseded_version_cannot_be_acknowledged_or_declined(
        client, world, superseded_sent_document, role_header, path_prefix, decision):
    headers = getattr(world, role_header)

    response = client.post(
        f"{API}/{path_prefix}/documents/{superseded_sent_document}/{decision}", headers=headers)

    assert response.status_code == 409, response.text
    assert "superseded" in response.json()["detail"]

    # המצב לא זז: הנקודה היא שלא נרשם אישור על נייר שהוחלף, לא רק שהתשובה 409
    doc = world.db.query(Document).filter(Document.document_id == superseded_sent_document).first()
    world.db.refresh(doc)
    assert doc.status == DocumentStatus.SENT
    assert doc.acknowledged_at is None
    assert doc.acknowledged_by_user_id is None


def test_trustee_pending_queue_excludes_superseded_versions(client, world, superseded_sent_document):
    """תור פעולה, לא היסטוריה: מסמך שהוחלף אינו פעולה שממתינה."""
    queue = client.get(f"{API}/trustee/documents/pending", headers=world.trustee_a)

    assert queue.status_code == 200
    assert [d["document_id"] for d in queue.json()] == []


def test_employee_still_sees_a_superseded_document_but_flagged(client, world, superseded_sent_document):
    """בשונה מתור הנאמן - רשימת העובד היא היסטוריה, ולכן היא ממשיכה להציג את
    הגרסה המיושנת. is_latest הוא מה שמאפשר למסך לסמן אותה ולחסום את הכפתור."""
    listed = client.get(f"{API}/employee/documents", headers=world.employee_a)

    rows = {d["document_id"]: d for d in listed.json()}
    assert superseded_sent_document in rows
    assert rows[superseded_sent_document]["is_latest"] is False


# --- סגירת חוב שלב 3: מסלול ההורדה המוצלח של העובד והנאמן ------------
# היה כיסוי אוטומטי למסלול השלילי בלבד (403 על טיוטה), ולמסלול המוצלח רק אצל
# האדמין. שלושת הנתיבים מגישים את אותו קובץ דרך אותה בדיקת בעלות, ולכן נתיב
# שנשבר בלי כיסוי הוא בדיוק P3.

@pytest.mark.parametrize("role_header,path_prefix", [
    ("employee_a", "employee"),
    ("trustee_a", "trustee"),
])
def test_employee_and_trustee_can_download_a_sent_document(
        client, world, sent_document, role_header, path_prefix):
    headers = getattr(world, role_header)

    response = client.get(f"{API}/{path_prefix}/documents/{sent_document}/download", headers=headers)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF"), "לא קובץ PDF אמיתי"

    from backend.app.models import AuditLog
    downloads = (world.db.query(AuditLog)
                 .filter(AuditLog.entity_id == sent_document, AuditLog.action == "DOWNLOADED")
                 .all())
    assert downloads, "הורדה לא נרשמה ב-audit"


def test_download_still_works_after_the_document_was_acknowledged(client, world, sent_document):
    """הטריגר מקפיא את שורת המסמך, וההורדה כותבת שורת audit בלבד. אם אי פעם
    ההורדה תתחיל לעדכן את documents עצמו, הבדיקה הזו תיפול - וזו הכוונה."""
    acked = client.post(f"{API}/employee/documents/{sent_document}/acknowledge",
                        headers=world.employee_a)
    assert acked.status_code == 200

    response = client.get(f"{API}/employee/documents/{sent_document}/download",
                          headers=world.employee_a)

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
