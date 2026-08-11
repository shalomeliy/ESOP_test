"""ייבוא - דריי-ראן בלבד (v0.9.1 שלב ב, PLAN.md §8 step 6).

לא כותב שורת דומיין אחת - dry_run() לעולם לא עושה db.add/db.commit על נתוני
החבילה עצמם. שני סוגי בדיקה: ברמת השירות (services.import_.dry_run ישירות,
לשליטה מדויקת בתרחיש) וברמת ה-HTTP (POST /admin/import/dry-run, לוודא
שהאנדפוינט עצמו לא כותב ומחזיר את הדוח הנכון).
"""

from datetime import date, timedelta

import pytest

from backend.app.types import utcnow
from backend.app.auth import hash_password
from backend.app.models import (
    Company, DataTransferDirection, DataTransferRun, DataTransferStatus, Employee,
    EmployeeStatus, Grant, GrantType, OptionPool, TaxRatesHistory, TaxRulePack,
    Trustee, User, UserRole, UserSession, VestingSchedule,
)
from backend.app.services.export import EXPORT_SCHEMA_VERSION
from backend.app.services import import_ as import_service

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
    """חברה A מאוכלסת (פול+עובד+נאמן+מענק+לוח הבשלה+חבילת מס), וחברה B ריקה
    לגמרי - כדי שאפשר יהיה להוכיח גם NEW (ייבוא לחברה ריקה) וגם התנגשות
    חוצה-חברות (מפתח שכבר שייך ל-A, מיובא כאילו הוא של B)."""
    db = db_session
    db.add_all([
        Company(company_id="IMP-COMP-A", name="Alpha", country_code="IL"),
        Company(company_id="IMP-COMP-B", name="Beta", country_code="IL"),
    ])
    db.add(Employee(employee_id="IMP-EMP-A1", company_id="IMP-COMP-A", first_name="Yossi",
                    last_name="Cohen", email="a1@alpha.example", country_code="IL",
                    status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1), birth_date=date(1990, 1, 1)))
    db.add(TaxRulePack(pack_id="IMP-PACK-A", country_code="IL",
                       grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                       effective_start_date=date(2000, 1, 1), calculation_method="FLAT_RATE",
                       official_source_url=SRC))
    db.flush()
    db.add(TaxRatesHistory(country_code="IL", grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                           effective_start_date=date(2000, 1, 1), capital_gains_rate=0.25,
                           official_source_url=SRC, pack_id="IMP-PACK-A"))
    db.add(OptionPool(pool_id="IMP-POOL-A", company_id="IMP-COMP-A", total_shares=10000.0,
                      allocated_shares=0.0, unallocated_shares=10000.0))
    db.add(Trustee(trustee_id="IMP-TRUST-A", company_id="IMP-COMP-A", name="Trustee Ltd",
                   registration_number="123"))
    db.flush()

    from backend.app.services.ledger import append_event, record_ownership
    record_ownership(db, aggregate_id="IMP-POOL-A", aggregate_type="OptionPool", company_id="IMP-COMP-A")
    append_event(db, event_type="POOL_BALANCE_ESTABLISHED", aggregate_type="OptionPool",
                aggregate_id="IMP-POOL-A",
                payload={"allocated_shares": 0.0, "unallocated_shares": 10000.0, "total_shares": 10000.0},
                effective_date=date(2020, 1, 1))
    db.commit()

    admin_a = _user(db, "IMP-U-ADMIN-A", UserRole.COMPANY_ADMIN, company_id="IMP-COMP-A")
    admin_b = _user(db, "IMP-U-ADMIN-B", UserRole.COMPANY_ADMIN, company_id="IMP-COMP-B")
    emp_a = _user(db, "IMP-U-EMP-A1", UserRole.EMPLOYEE, employee_id="IMP-EMP-A1")
    from types import SimpleNamespace
    return SimpleNamespace(db=db, admin_a=_token(db, admin_a), admin_b=_token(db, admin_b),
                           emp_a=_token(db, emp_a))


def _export_bundle(client, headers) -> dict:
    run_id = client.post(f"{API}/admin/export", headers=headers).json()["run_id"]
    return client.get(f"{API}/admin/export/{run_id}/download", headers=headers).json()


def _upload_dry_run(client, headers, bundle: dict, filename: str = "export.json"):
    import json
    return client.post(f"{API}/admin/import/dry-run", headers=headers,
                       files={"file": (filename, json.dumps(bundle).encode("utf-8"), "application/json")})


def _grant_and_approve(client, world) -> str:
    """מייצר מענק+מסמך+בקשת מימוש מאושרת אמיתיים בחברה A, כדי שהבאנדל
    שמיוצא ממנה יכלול grants/vesting_schedules/documents/exercise_requests/
    exercise_tax_records/ledger_events/audit_log אמיתיים - לא רק pool+employee."""
    world.db.query(Employee).filter(Employee.employee_id == "IMP-EMP-A1").update({"birth_date": date(1990, 1, 1)})
    world.db.commit()
    grant_resp = client.post(f"{API}/admin/grants", headers=world.admin_a, json={
        "employee_id": "IMP-EMP-A1", "pool_id": "IMP-POOL-A",
        "grant_type": "IL_102_CAPITAL_GAINS", "total_options": 4800.0,
        "exercise_price": 1.0, "grant_date": "2024-01-01", "cliff_months": 12, "total_months": 48,
    })
    assert grant_resp.status_code == 200, grant_resp.text
    grant_id = grant_resp.json()["grant_id"]
    req_resp = client.post(f"{API}/employee/exercise-requests", headers=world.emp_a,
                           json={"grant_id": grant_id, "options_to_exercise": 100.0})
    assert req_resp.status_code == 200, req_resp.text
    request_id = req_resp.json()["request_id"]
    approve_resp = client.patch(f"{API}/admin/exercise-requests/{request_id}",
                                headers=world.admin_a, json={"approve": True})
    assert approve_resp.status_code == 200, approve_resp.text
    return grant_id


# ===================================================================
# ברמת ה-HTTP - האנדפוינט עצמו
# ===================================================================

def test_dry_run_of_your_own_export_reimported_into_yourself_is_all_skip_existing(client, world):
    _grant_and_approve(client, world)
    bundle = _export_bundle(client, world.admin_a)

    response = _upload_dry_run(client, world.admin_a, bundle)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["rows_failed"] == 0
    assert body["rows_new"] == 0, "כל השורות כבר שייכות לחברה הזו - הכל אמור להיות SKIP_EXISTING"
    assert body["rows_skipped_existing"] == body["rows_attempted"]
    assert body["errors"] == []


def _synthetic_employee_bundle(*, employee_id: str, company_id: str) -> dict:
    """באנדל מינימלי תקין (לא A's own data, כדי שלא יתנגש עם מה ש-A כבר
    מחזיקה) - עובד יחיד עם employee_id חדש שלא קיים בשום מקום ב-DB עדיין."""
    bundle = _export_bundle_shape_only()
    bundle["tables"]["employees"] = [{
        "employee_id": employee_id, "company_id": company_id, "first_name": "New",
        "last_name": "Person", "email": f"{employee_id.lower()}@x.example", "country_code": "IL",
        "status": "ACTIVE", "hire_date": "2020-01-01", "termination_date": None,
        "birth_date": None, "national_id": None,
    }]
    return bundle


def _export_bundle_shape_only() -> dict:
    empty_tables = ["employees", "option_pools", "trustees", "grants", "vesting_schedules",
                    "documents", "exercise_requests", "exercise_tax_records", "ledger_events",
                    "audit_log", "notification_preferences", "notification_dismissals",
                    "tax_rule_packs", "tax_rates_history", "income_tax_brackets"]
    return {
        "export_schema_version": EXPORT_SCHEMA_VERSION, "company_id": "IMP-COMP-A",
        "contains_demo_tax_data": False,
        "tables": {"companies": [{"company_id": "IMP-COMP-A", "name": "Alpha", "country_code": "IL",
                                  "is_active": True, "created_at": None}],
                  **{name: [] for name in empty_tables}},
    }


def test_dry_run_makes_zero_db_writes(client, world):
    """הבדיקה המרכזית של task #6: ייבוא לחברה B (ריקה) של עובד חדש לגמרי
    (לא קיים בשום מקום ב-DB) אמור לסווג אותו כ-NEW - אבל אף Employee לא
    נכתב בפועל לטבלה."""
    bundle = _synthetic_employee_bundle(employee_id="IMP-EMP-FRESH", company_id="IMP-COMP-A")
    employees_before = world.db.query(Employee).count()

    response = _upload_dry_run(client, world.admin_b, bundle)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows_failed"] == 0
    assert body["rows_new"] == 1

    assert world.db.query(Employee).count() == employees_before
    assert world.db.query(Employee).filter(Employee.employee_id == "IMP-EMP-FRESH").first() is None


def test_import_ignores_company_id_in_the_file_and_forces_the_callers_company(client, world):
    # השורה מצהירה על company_id="IMP-COMP-A" (מזויף/לא רלוונטי) בזמן שהיא
    # מיובאת בפועל ל-B - הסיווג לא אמור להיות מושפע מזה, כי הוא מבוסס רק על
    # קיום המפתח הראשי מול scope של B, לא על מה שהקובץ טוען על עצמו.
    bundle = _synthetic_employee_bundle(employee_id="IMP-EMP-FRESH2", company_id="IMP-COMP-A")

    response = _upload_dry_run(client, world.admin_b, bundle)
    assert response.status_code == 200, response.text
    assert response.json()["rows_failed"] == 0
    assert response.json()["rows_new"] == 1


def test_cross_company_id_collision_is_rejected_not_upserted(client, world):
    """world.admin_a מנסה "לייבא" (בטעות/בזדון) חבילה שבה employee_id זהה
    כבר קיים תחת B - כלומר A מנסה לדרוס נתון ששייך לחברה אחרת."""
    bundle = _export_bundle(client, world.admin_a)
    # שותל עובד עם אותו employee_id תחת B מראש - עכשיו ייבוא של אותה חבילה
    # לתוך A עצמה לא ייתקל בזה (A כבר הבעלים), אז מדמים את התרחיש ההפוך:
    # B מנסה לייבא בעצמו את מה שבבאנדל, אבל employee_id הזה כבר "תפוס" ב-A.
    response = _upload_dry_run(client, world.admin_b, bundle)
    # ה-employee_id הזה (IMP-EMP-A1) כבר קיים ב-DB אבל שייך ל-A, לא ל-B.
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["rows_failed"] > 0
    employee_errors = [e for e in body["errors"] if e["table"] == "employees"]
    assert employee_errors, "שורת העובד שכבר שייכת ל-A חייבת להופיע כשגיאה כשמנסים לייבא אותה ל-B"
    assert "different company" in employee_errors[0]["error"]


def test_dry_run_endpoint_persists_a_data_transfer_run_row(client, world):
    bundle = _export_bundle(client, world.admin_a)
    response = _upload_dry_run(client, world.admin_a, bundle)
    run_id = response.json()["run_id"]

    run = world.db.query(DataTransferRun).filter(DataTransferRun.run_id == run_id).one()
    assert run.direction == DataTransferDirection.IMPORT_DRY_RUN
    assert run.target_company_id == "IMP-COMP-A"
    assert run.status == DataTransferStatus.SUCCESS


def test_dry_run_rejects_mismatched_schema_version(client, world):
    bundle = _export_bundle(client, world.admin_a)
    bundle["export_schema_version"] = EXPORT_SCHEMA_VERSION + 1
    response = _upload_dry_run(client, world.admin_a, bundle)
    assert response.status_code == 422


def test_dry_run_rejects_a_file_over_the_size_cap_before_parsing(client, world, monkeypatch):
    monkeypatch.setattr("backend.app.services.import_.MAX_IMPORT_FILE_BYTES", 10)
    monkeypatch.setattr("backend.app.api.export.MAX_IMPORT_FILE_BYTES", 10)
    bundle = _export_bundle(client, world.admin_a)
    response = _upload_dry_run(client, world.admin_a, bundle)
    assert response.status_code == 413


# ===================================================================
# ברמת השירות - services.import_.dry_run ישירות, לשליטה מדויקת בתרחיש
# ===================================================================

def _minimal_bundle(**table_overrides) -> dict:
    tables = {
        "companies": [{"company_id": "SVC-COMP", "name": "Svc", "country_code": "IL",
                      "is_active": True, "created_at": None}],
        "employees": [], "option_pools": [], "trustees": [], "grants": [],
        "vesting_schedules": [], "documents": [], "exercise_requests": [],
        "exercise_tax_records": [], "ledger_events": [], "audit_log": [],
        "notification_preferences": [], "notification_dismissals": [],
        "tax_rule_packs": [], "tax_rates_history": [], "income_tax_brackets": [],
    }
    tables.update(table_overrides)
    return {"export_schema_version": EXPORT_SCHEMA_VERSION, "company_id": "SVC-COMP",
           "contains_demo_tax_data": False, "tables": tables}


def test_new_rows_with_resolvable_fks_are_classified_as_new(db_session):
    world_company = Company(company_id="SVC-TARGET", name="Target", country_code="IL")
    db_session.add(world_company)
    db_session.commit()

    bundle = _minimal_bundle(
        option_pools=[{"pool_id": "SVC-POOL-1", "company_id": "SVC-COMP", "total_shares": 100.0,
                      "allocated_shares": 0.0, "unallocated_shares": 100.0, "created_at": None}],
        employees=[{"employee_id": "SVC-EMP-1", "company_id": "SVC-COMP", "first_name": "A", "last_name": "B",
                   "email": "svc@x.example", "country_code": "IL", "status": "ACTIVE",
                   "hire_date": "2020-01-01", "termination_date": None, "birth_date": None,
                   "national_id": None}],
        grants=[{"grant_id": "SVC-GRANT-1", "employee_id": "SVC-EMP-1", "pool_id": "SVC-POOL-1",
                "trustee_id": None, "grant_date": "2024-01-01", "grant_type": "IL_102_CAPITAL_GAINS",
                "total_options": 100.0, "exercise_price": 1.0, "currency": "USD",
                "trustee_deposit_date": None, "post_termination_window_days": 90}],
    )

    report = import_service.dry_run(db_session, bundle, "SVC-TARGET")
    assert report.valid is True
    statuses = {(o.table, o.row_id): o.status for o in report.outcomes}
    assert statuses[("option_pools", "SVC-POOL-1")] == "NEW"
    assert statuses[("employees", "SVC-EMP-1")] == "NEW"
    assert statuses[("grants", "SVC-GRANT-1")] == "NEW"


def test_grant_referencing_an_unknown_pool_is_rejected(db_session):
    db_session.add(Company(company_id="SVC-TARGET2", name="Target2", country_code="IL"))
    db_session.commit()

    bundle = _minimal_bundle(
        grants=[{"grant_id": "SVC-GRANT-ORPHAN", "employee_id": "SVC-EMP-NOPE", "pool_id": "SVC-POOL-NOPE",
                "trustee_id": None, "grant_date": "2024-01-01", "grant_type": "IL_102_CAPITAL_GAINS",
                "total_options": 100.0, "exercise_price": 1.0, "currency": "USD",
                "trustee_deposit_date": None, "post_termination_window_days": 90}],
    )
    report = import_service.dry_run(db_session, bundle, "SVC-TARGET2")
    assert report.valid is False
    assert report.rows_failed == 1
    assert "pool_id" in report.errors[0].error


def test_tax_rate_resolves_to_pack_in_the_same_batch_by_natural_key_not_pack_id(db_session):
    db_session.add(Company(company_id="SVC-TARGET3", name="Target3", country_code="IL"))
    db_session.commit()

    bundle = _minimal_bundle(
        tax_rule_packs=[{"country_code": "IL", "grant_type": "IL_102_CAPITAL_GAINS",
                        "effective_start_date": "2000-01-01", "calculation_method": "FLAT_RATE",
                        "official_source_url": SRC, "created_at": None}],
        tax_rates_history=[{"tax_rule_id": "whatever", "country_code": "IL",
                           "grant_type": "IL_102_CAPITAL_GAINS", "effective_start_date": "2000-01-01",
                           "capital_gains_rate": 0.25, "official_source_url": SRC}],
    )
    assert "pack_id" not in bundle["tables"]["tax_rates_history"][0], "אין pack_id בקלט - זו בדיוק הנקודה"
    report = import_service.dry_run(db_session, bundle, "SVC-TARGET3")
    assert report.valid is True
    assert report.rows_new == 2


def test_tax_rate_without_a_matching_pack_is_rejected(db_session):
    db_session.add(Company(company_id="SVC-TARGET4", name="Target4", country_code="IL"))
    db_session.commit()
    bundle = _minimal_bundle(
        tax_rates_history=[{"tax_rule_id": "whatever", "country_code": "IL",
                           "grant_type": "IL_102_CAPITAL_GAINS", "effective_start_date": "1999-01-01",
                           "capital_gains_rate": 0.25, "official_source_url": SRC}],
    )
    report = import_service.dry_run(db_session, bundle, "SVC-TARGET4")
    assert report.valid is False
    assert "no matching tax_rule_packs" in report.errors[0].error


def test_schema_version_mismatch_is_rejected_before_any_row_is_touched():
    bundle = _minimal_bundle()
    bundle["export_schema_version"] = 999
    raw = __import__("json").dumps(bundle).encode("utf-8")
    with pytest.raises(import_service.ImportSchemaVersionMismatch):
        import_service.parse_and_validate_bundle_shape(raw)


def test_bundle_without_a_companies_row_is_rejected():
    bundle = _minimal_bundle(companies=[])
    raw = __import__("json").dumps(bundle).encode("utf-8")
    with pytest.raises(import_service.InvalidImportBundleError):
        import_service.parse_and_validate_bundle_shape(raw)


def test_oversized_file_is_rejected_before_json_parsing():
    with pytest.raises(import_service.ImportFileTooLargeError):
        import_service.assert_file_size_within_limit(import_service.MAX_IMPORT_FILE_BYTES + 1)


def test_deeply_nested_json_is_rejected():
    payload = {}
    node = payload
    for _ in range(import_service.MAX_IMPORT_JSON_DEPTH + 5):
        node["nested"] = {}
        node = node["nested"]
    with pytest.raises(import_service.ImportJsonTooDeepError):
        import_service.assert_json_depth_within_limit(payload)


def test_too_many_rows_is_rejected(monkeypatch):
    monkeypatch.setattr(import_service, "IMPORT_MAX_ROWS", 1)
    bundle = _minimal_bundle(
        employees=[{"employee_id": f"SVC-EMP-{i}", "company_id": "SVC-COMP"} for i in range(5)],
    )
    with pytest.raises(import_service.ImportTooManyRowsError):
        import_service.assert_row_count_within_limit(bundle)


# ===================================================================
# LedgerEvent - אידמפוטנטיות על (aggregate_id, sequence_no), לא event_id
# (decision 5), ודחיית aggregate יתום.
# ===================================================================

def test_reimporting_the_same_ledger_event_is_idempotent_on_aggregate_and_sequence(db_session):
    from backend.app.services.ledger import append_event, record_ownership
    db_session.add(Company(company_id="SVC-LEDG-TARGET", name="T", country_code="IL"))
    db_session.add(OptionPool(pool_id="SVC-LEDG-POOL", company_id="SVC-LEDG-TARGET", total_shares=100.0,
                              allocated_shares=0.0, unallocated_shares=100.0))
    db_session.flush()
    record_ownership(db_session, aggregate_id="SVC-LEDG-POOL", aggregate_type="OptionPool",
                     company_id="SVC-LEDG-TARGET")
    event = append_event(db_session, event_type="POOL_BALANCE_ESTABLISHED", aggregate_type="OptionPool",
                         aggregate_id="SVC-LEDG-POOL",
                         payload={"allocated_shares": 0.0, "unallocated_shares": 100.0, "total_shares": 100.0},
                         effective_date=date(2020, 1, 1))
    db_session.commit()

    bundle = _minimal_bundle(
        ledger_events=[{"event_id": "DIFFERENT-EVENT-ID-FROM-SOURCE", "event_type": "POOL_BALANCE_ESTABLISHED",
                       "aggregate_type": "OptionPool", "aggregate_id": "SVC-LEDG-POOL",
                       "payload": "{}", "effective_date": "2020-01-01", "recorded_at": "2020-01-01T00:00:00",
                       "actor_user_id": None, "sequence_no": event.sequence_no, "corrects_event_id": None,
                       "schema_version": 1, "source": "LIVE"}],
    )
    report = import_service.dry_run(db_session, bundle, "SVC-LEDG-TARGET")
    assert report.valid is True
    assert report.outcomes[0].status == "SKIP_EXISTING", (
        "אידמפוטנטיות על (aggregate_id, sequence_no) - גם כש-event_id שונה (מקור אחר)"
    )


def test_ledger_event_with_unknown_aggregate_is_rejected(db_session):
    db_session.add(Company(company_id="SVC-LEDG-TARGET2", name="T2", country_code="IL"))
    db_session.commit()
    bundle = _minimal_bundle(
        ledger_events=[{"event_id": "EVT-ORPHAN", "event_type": "POOL_BALANCE_ESTABLISHED",
                       "aggregate_type": "OptionPool", "aggregate_id": "DOES-NOT-EXIST-ANYWHERE",
                       "payload": "{}", "effective_date": "2020-01-01", "recorded_at": "2020-01-01T00:00:00",
                       "actor_user_id": None, "sequence_no": 1, "corrects_event_id": None,
                       "schema_version": 1, "source": "LIVE"}],
    )
    report = import_service.dry_run(db_session, bundle, "SVC-LEDG-TARGET2")
    assert report.valid is False
    assert "not found" in report.errors[0].error
