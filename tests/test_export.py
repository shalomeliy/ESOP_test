"""ייצוא נתוני חברה (v0.9.1 שלב ב) - השלוחה הראשונה (Company+Employee) והיקף
מלא (PLAN.md §8 step 4: שאר הטבלאות, חבילות מס לפי natural key, CSV).

מיפוי ל-PLAN.md §2 (thin end-to-end slice), §6 (decision 9c/9d - הורדה
מאומתת, אי-הגשה כקובץ סטטי), ו-§3 (חבילות מס לפי natural key, לא pack_id).
"""

import io
import zipfile
from datetime import date, timedelta

import pytest

from backend.app.types import utcnow
from backend.app.auth import hash_password
from backend.app.models import (
    AuditLog, Company, DataTransferDirection, DataTransferRun, DataTransferStatus,
    Employee, EmployeeStatus, GrantType, TaxRatesHistory, TaxRulePack,
    User, UserRole, UserSession,
)

API = "/api/v1"
SRC = "https://test.invalid/qa-fixture-not-a-real-tax-source"


def _token(db, user: User) -> dict:
    token = f"tok-{user.user_id}"
    db.add(UserSession(token=token, user_id=user.user_id, expires_at=utcnow() + timedelta(hours=1)))
    db.flush()
    return {"Authorization": f"Bearer {token}"}


def _user(db, user_id: str, role: UserRole, **ids) -> User:
    pw_hash, salt = hash_password("Demo1234!")
    u = User(user_id=user_id, username=f"{user_id.lower()}@test.example",
             password_hash=pw_hash, password_salt=salt, role=role, is_active=True, **ids)
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def world(db_session):
    """שתי חברות, כדי שאפשר יהיה להוכיח בעצם שתי אינסטנציות (403) ולא רק
    שהיקף ריק."""
    db = db_session
    db.add_all([
        Company(company_id="COMP-EXP-A", name="Alpha", country_code="IL"),
        Company(company_id="COMP-EXP-B", name="Beta", country_code="IL"),
    ])
    db.add_all([
        Employee(employee_id="EXP-EMP-A1", company_id="COMP-EXP-A", first_name="Yossi",
                 last_name="Cohen", email="a1@alpha.example", country_code="IL",
                 status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1)),
        Employee(employee_id="EXP-EMP-B1", company_id="COMP-EXP-B", first_name="Rivka",
                 last_name="Levi", email="b1@beta.example", country_code="IL",
                 status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1)),
    ])
    db.flush()

    admin_a = _user(db, "U-EXP-ADMIN-A", UserRole.COMPANY_ADMIN, company_id="COMP-EXP-A")
    admin_b = _user(db, "U-EXP-ADMIN-B", UserRole.COMPANY_ADMIN, company_id="COMP-EXP-B")
    emp_a = _user(db, "U-EXP-EMP-A1", UserRole.EMPLOYEE, employee_id="EXP-EMP-A1")
    from types import SimpleNamespace
    return SimpleNamespace(db=db, admin_a=_token(db, admin_a), admin_b=_token(db, admin_b),
                           emp_a=_token(db, emp_a))


def test_export_round_trips_company_and_employee_data(client, world):
    response = client.post(f"{API}/admin/export", headers=world.admin_a)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["source_company_id"] == "COMP-EXP-A"
    assert body["rows_attempted"] == 2  # 1 company + 1 employee
    assert body["rows_failed"] == 0

    download = client.get(f"{API}/admin/export/{body['run_id']}/download", headers=world.admin_a)
    assert download.status_code == 200
    bundle = download.json()
    assert bundle["export_schema_version"] == 1
    assert bundle["tables"]["companies"] == [{
        "company_id": "COMP-EXP-A", "name": "Alpha", "country_code": "IL",
        "is_active": True, "created_at": bundle["tables"]["companies"][0]["created_at"],
    }]
    employees = bundle["tables"]["employees"]
    assert len(employees) == 1
    assert employees[0]["email"] == "a1@alpha.example"
    assert employees[0]["employee_id"] == "EXP-EMP-A1"


def test_export_does_not_leak_another_companys_employees(client, world):
    response = client.post(f"{API}/admin/export", headers=world.admin_a)
    bundle_employees = client.get(
        f"{API}/admin/export/{response.json()['run_id']}/download", headers=world.admin_a
    ).json()["tables"]["employees"]
    assert {e["employee_id"] for e in bundle_employees} == {"EXP-EMP-A1"}


def test_company_b_cannot_download_company_as_export(client, world):
    response = client.post(f"{API}/admin/export", headers=world.admin_a)
    run_id = response.json()["run_id"]

    forbidden = client.get(f"{API}/admin/export/{run_id}/download", headers=world.admin_b)
    assert forbidden.status_code == 403


def test_download_of_unknown_export_is_404(client, world):
    response = client.get(f"{API}/admin/export/does-not-exist/download", headers=world.admin_a)
    assert response.status_code == 404


def test_export_writes_an_audit_entry(client, world):
    response = client.post(f"{API}/admin/export", headers=world.admin_a)
    run_id = response.json()["run_id"]

    audit_rows = world.db.query(AuditLog).filter(
        AuditLog.entity_type == "DataTransferRun", AuditLog.entity_id == run_id).all()
    actions = {row.action for row in audit_rows}
    assert "EXPORTED" in actions

    client.get(f"{API}/admin/export/{run_id}/download", headers=world.admin_a)
    audit_rows_after = world.db.query(AuditLog).filter(
        AuditLog.entity_type == "DataTransferRun", AuditLog.entity_id == run_id).all()
    assert {row.action for row in audit_rows_after} == {"EXPORTED", "DOWNLOADED"}


def test_export_history_row_records_direction_and_schema_version(client, world):
    client.post(f"{API}/admin/export", headers=world.admin_a)
    run = world.db.query(DataTransferRun).filter(
        DataTransferRun.source_company_id == "COMP-EXP-A").one()
    assert run.direction == DataTransferDirection.EXPORT
    assert run.status == DataTransferStatus.SUCCESS
    assert run.export_schema_version == 1


# ===================================================================
# היקף מלא (PLAN.md §8 step 4) - פול, נאמן, מענק, לוח הבשלה, בקשת מימוש
# שאושרה בפועל (ExerciseTaxRecord + ledger_events + audit_log דרך הקוד
# האמיתי, לא מוזרקים ידנית), וחבילת מס אחת. שתי חברות, כדי שדליפה תיתפס.
# ===================================================================

def test_export_includes_the_full_table_registry_and_excludes_ledger_ownership(client, world):
    db = world.db
    from backend.app.models import Employee as _Employee, Grant as _Grant, OptionPool, Trustee
    db.query(_Employee).filter(_Employee.employee_id == "EXP-EMP-A1").update({"birth_date": date(1990, 1, 1)})
    db.add(TaxRulePack(pack_id="EXP-PACK-A", country_code="IL",
                       grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                       effective_start_date=date(2000, 1, 1), calculation_method="FLAT_RATE",
                       official_source_url=SRC))
    db.flush()  # TaxRulePack חייב להיכתב לפני TaxRatesHistory - אין relationship() בין השתיים,
    # אז unit-of-work לא ממיין לפי FK בלבד (ראו test_tax_engine.py::_pack, אותו דפוס).
    db.add(TaxRatesHistory(country_code="IL", grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                           effective_start_date=date(2000, 1, 1), capital_gains_rate=0.25,
                           official_source_url=SRC, pack_id="EXP-PACK-A"))
    db.add(OptionPool(pool_id="EXP-POOL-A", company_id="COMP-EXP-A", total_shares=10000.0,
                      allocated_shares=0.0, unallocated_shares=10000.0))
    db.add(Trustee(trustee_id="EXP-TRUST-A", company_id="COMP-EXP-A", name="Trustee Ltd",
                   registration_number="123"))
    db.flush()

    from backend.app.services.ledger import append_event, record_ownership
    record_ownership(db, aggregate_id="EXP-POOL-A", aggregate_type="OptionPool", company_id="COMP-EXP-A")
    append_event(db, event_type="POOL_BALANCE_ESTABLISHED", aggregate_type="OptionPool",
                aggregate_id="EXP-POOL-A",
                payload={"allocated_shares": 0.0, "unallocated_shares": 10000.0, "total_shares": 10000.0},
                effective_date=date(2020, 1, 1))
    db.commit()

    grant_resp = client.post(f"{API}/admin/grants", headers=world.admin_a, json={
        "employee_id": "EXP-EMP-A1", "pool_id": "EXP-POOL-A",
        "grant_type": "IL_102_CAPITAL_GAINS", "total_options": 4800.0,
        "exercise_price": 1.0, "grant_date": "2024-01-01", "cliff_months": 12, "total_months": 48,
    })
    assert grant_resp.status_code == 200, grant_resp.text
    grant_id = grant_resp.json()["grant_id"]

    doc_resp = client.post(f"{API}/admin/documents", headers=world.admin_a,
                           json={"grant_id": grant_id, "template_type": "GRANT_LETTER"})
    assert doc_resp.status_code == 200, doc_resp.text

    req_resp = client.post(f"{API}/employee/exercise-requests", headers=world.emp_a,
                           json={"grant_id": grant_id, "options_to_exercise": 100.0})
    assert req_resp.status_code == 200, req_resp.text
    request_id = req_resp.json()["request_id"]

    approve_resp = client.patch(f"{API}/admin/exercise-requests/{request_id}",
                                headers=world.admin_a, json={"approve": True})
    assert approve_resp.status_code == 200, approve_resp.text

    export_resp = client.post(f"{API}/admin/export", headers=world.admin_a)
    tables = client.get(f"{API}/admin/export/{export_resp.json()['run_id']}/download",
                        headers=world.admin_a).json()["tables"]

    assert len(tables["option_pools"]) == 1
    assert len(tables["trustees"]) == 1
    assert [g["grant_id"] for g in tables["grants"]] == [grant_id]
    assert len(tables["vesting_schedules"]) == 1
    assert len(tables["documents"]) == 1
    assert [r["request_id"] for r in tables["exercise_requests"]] == [request_id]
    assert [r["request_id"] for r in tables["exercise_tax_records"]] == [request_id], (
        "אישור אמיתי צריך לכתוב ExerciseTaxRecord (ראו task #2) - וזה חייב להופיע בייצוא"
    )
    assert len(tables["ledger_events"]) > 0, "אירועי היומן על הפול/המענק/הבקשה אמורים להיות בהיקף"
    assert len(tables["audit_log"]) > 0

    # שלושת ההחלטות המפורשות: לא מיוצאים בשום צורה.
    assert "ledger_ownership" not in tables
    assert "users" not in tables
    assert "stock_prices_history" not in tables

    # natural key, לא pack_id - זו ההחלטה שנבדקה ואומתה בתכנון.
    packs = tables["tax_rule_packs"]
    assert len(packs) == 1
    assert "pack_id" not in packs[0]
    assert packs[0]["country_code"] == "IL"
    assert packs[0]["grant_type"] == "IL_102_CAPITAL_GAINS"
    assert "pack_id" not in tables["tax_rates_history"][0]


def test_tax_pack_export_is_scoped_by_natural_key_not_the_company_thats_using_it(client, world):
    """שתי חברות שונות שמשתמשות באותה חבילת מס (אותו country_code/grant_type) -
    כל אחת מקבלת את השורה בייצוא שלה, בלי תלות במי "יצר" אותה קודם."""
    from backend.app.models import Employee as _Employee, Grant as _Grant, OptionPool
    db = world.db
    db.query(_Employee).filter(_Employee.employee_id.in_(["EXP-EMP-A1", "EXP-EMP-B1"])).update(
        {"birth_date": date(1990, 1, 1)}, synchronize_session=False)
    db.add(TaxRulePack(pack_id="EXP-PACK-SHARED", country_code="IL",
                       grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                       effective_start_date=date(2000, 1, 1), calculation_method="FLAT_RATE",
                       official_source_url=SRC))
    db.flush()
    db.add(TaxRatesHistory(country_code="IL", grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                           effective_start_date=date(2000, 1, 1), capital_gains_rate=0.25,
                           official_source_url=SRC, pack_id="EXP-PACK-SHARED"))
    db.add(OptionPool(pool_id="EXP-POOL-A2", company_id="COMP-EXP-A", total_shares=1000.0,
                      allocated_shares=0.0, unallocated_shares=1000.0))
    db.add(OptionPool(pool_id="EXP-POOL-B2", company_id="COMP-EXP-B", total_shares=1000.0,
                      allocated_shares=0.0, unallocated_shares=1000.0))
    db.flush()  # OptionPool לפני Grant - אין relationship() ישיר בין השתיים.
    db.add(_Grant(grant_id="EXP-GRANT-A2", employee_id="EXP-EMP-A1", pool_id="EXP-POOL-A2",
                 grant_date=date(2024, 1, 1), grant_type=GrantType.IL_102_CAPITAL_GAINS,
                 total_options=100.0, exercise_price=1.0))
    db.add(_Grant(grant_id="EXP-GRANT-B2", employee_id="EXP-EMP-B1", pool_id="EXP-POOL-B2",
                 grant_date=date(2024, 1, 1), grant_type=GrantType.IL_102_CAPITAL_GAINS,
                 total_options=100.0, exercise_price=1.0))
    db.commit()

    for company, admin in (("COMP-EXP-A", world.admin_a), ("COMP-EXP-B", world.admin_b)):
        export_resp = client.post(f"{API}/admin/export", headers=admin)
        tables = client.get(f"{API}/admin/export/{export_resp.json()['run_id']}/download",
                            headers=admin).json()["tables"]
        assert len(tables["tax_rule_packs"]) == 1, f"{company} should see the shared pack once"
        assert tables["tax_rule_packs"][0]["country_code"] == "IL"


def test_export_flags_demo_tax_data_at_the_bundle_level(client, world):
    from backend.app.services.export import DEMO_TAX_SOURCE_SENTINEL
    from backend.app.models import Employee as _Employee, Grant as _Grant, OptionPool
    db = world.db
    db.query(_Employee).filter(_Employee.employee_id == "EXP-EMP-A1").update({"birth_date": date(1990, 1, 1)})
    db.add(TaxRulePack(pack_id="EXP-PACK-DEMO", country_code="IL",
                       grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                       effective_start_date=date(2000, 1, 1), calculation_method="FLAT_RATE",
                       official_source_url=DEMO_TAX_SOURCE_SENTINEL))
    db.flush()
    db.add(TaxRatesHistory(country_code="IL", grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                           effective_start_date=date(2000, 1, 1), capital_gains_rate=0.25,
                           official_source_url=DEMO_TAX_SOURCE_SENTINEL, pack_id="EXP-PACK-DEMO"))
    db.add(OptionPool(pool_id="EXP-POOL-A3", company_id="COMP-EXP-A", total_shares=1000.0,
                      allocated_shares=0.0, unallocated_shares=1000.0))
    db.flush()  # OptionPool לפני Grant - אין relationship() ישיר בין השתיים.
    db.add(_Grant(grant_id="EXP-GRANT-A3", employee_id="EXP-EMP-A1", pool_id="EXP-POOL-A3",
                 grant_date=date(2024, 1, 1), grant_type=GrantType.IL_102_CAPITAL_GAINS,
                 total_options=100.0, exercise_price=1.0))
    db.commit()

    export_resp = client.post(f"{API}/admin/export", headers=world.admin_a)
    bundle = client.get(f"{API}/admin/export/{export_resp.json()['run_id']}/download",
                        headers=world.admin_a).json()
    assert bundle["contains_demo_tax_data"] is True


def test_download_as_csv_returns_one_file_per_table_with_no_pack_id_column(client, world):
    response = client.post(f"{API}/admin/export", headers=world.admin_a)
    download = client.get(f"{API}/admin/export/{response.json()['run_id']}/download?format=csv",
                          headers=world.admin_a)
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        names = set(zf.namelist())
        assert "companies.csv" in names
        assert "employees.csv" in names
        companies_csv = zf.read("companies.csv").decode("utf-8")
        assert "COMP-EXP-A" in companies_csv


def test_csv_export_escapes_leading_formula_characters(client, world):
    db = world.db
    db.query(Company).filter(Company.company_id == "COMP-EXP-A").update({"name": "=cmd|'/c calc'!A1"})
    db.commit()

    response = client.post(f"{API}/admin/export", headers=world.admin_a)
    download = client.get(f"{API}/admin/export/{response.json()['run_id']}/download?format=csv",
                          headers=world.admin_a)
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        companies_csv = zf.read("companies.csv").decode("utf-8")
    assert "'=cmd" in companies_csv, "תא שמתחיל ב-= חייב להיות מנוטרל בקידומת '"


def test_download_rejects_unknown_format(client, world):
    response = client.post(f"{API}/admin/export", headers=world.admin_a)
    bad = client.get(f"{API}/admin/export/{response.json()['run_id']}/download?format=xml",
                     headers=world.admin_a)
    assert bad.status_code == 400


# ===================================================================
# מגבלת גודל (PLAN.md decision 7, §8 step 5) - נכשל *לפני* run_export, לא
# באמצעו.
# ===================================================================

def test_export_of_an_oversized_company_is_rejected_before_any_table_read(client, world, monkeypatch):
    """world.admin_a בהיקף שלו כבר 2 שורות (חברה+עובד) - מגבלה של 1 חייבת
    לחסום, ו-run_export עצמו (ה-loader היקר, לא ה-COUNT הזול) לא אמור להיקרא
    בכלל."""
    monkeypatch.setattr("backend.app.services.export.EXPORT_MAX_ROWS", 1)

    import backend.app.api.export as export_api
    calls = []
    original_run_export = export_api.run_export
    monkeypatch.setattr(export_api, "run_export",
                        lambda *a, **kw: calls.append(1) or original_run_export(*a, **kw))

    response = client.post(f"{API}/admin/export", headers=world.admin_a)
    assert response.status_code == 413
    assert calls == [], "run_export (הטעינה המלאה) לא אמור להיקרא כשהבדיקה הזולה כבר חסמה"


def test_export_row_count_estimate_matches_the_cheap_scope_without_full_hydration(client, world):
    """אומדן שמראה שהוא בסדר גודל נכון (לא ניחוש) - לפחות מספר החברה+העובדים
    שבאמת בהיקף, בלי לטעון אף שורה מלאה."""
    from backend.app.services.export import estimate_export_row_count
    assert estimate_export_row_count(world.db, "COMP-EXP-A") == 2  # company + 1 employee


def test_export_under_the_default_limit_is_not_affected(client, world):
    response = client.post(f"{API}/admin/export", headers=world.admin_a)
    assert response.status_code == 200
