"""ייבוא - commit (v0.9.1 שלב ב, PLAN.md §8 step 7).

dry_run (task #6) קובע מה מותר; commit (task #7) הוא הכתיבה בפועל. כל בדיקה
כאן קוראת ל-services.import_.commit ישירות (ברמת השירות, לא HTTP) - ה-
endpoint עצמו (POST /admin/import/commit) הוא task #8, שגם מוסיף את אכיפת
שני-השלבים (409 על דריי-ראן מיושן/מנוצל). ראו HANDOFF.md.
"""

import json
from datetime import date, timedelta

import pytest

from backend.app.types import utcnow
from backend.app.auth import hash_password
from backend.app.models import (
    AuditLog, Company, Document, Employee, EmployeeStatus, ExerciseRequest,
    ExerciseTaxRecord, Grant, GrantType, LedgerEvent, NotificationPreference,
    OptionPool, TaxRatesHistory, TaxRulePack, Trustee, User, UserRole, UserSession,
    VestingSchedule,
)
from backend.app.services.export import EXPORT_SCHEMA_VERSION
from backend.app.services import import_ as import_service
from backend.app.services.ledger import append_event, record_ownership

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
    """חברה A מאוכלסת, וחברה B ריקה לגמרי - אותו shape בדיוק כמו
    test_import_dry_run.py, כדי ש-commit ייבחן על אותה טופולוגיה שכבר
    אומתה ב-dry_run."""
    db = db_session
    db.add_all([
        Company(company_id="CMT-COMP-A", name="Alpha", country_code="IL"),
        Company(company_id="CMT-COMP-B", name="Beta", country_code="IL"),
    ])
    db.add(Employee(employee_id="CMT-EMP-A1", company_id="CMT-COMP-A", first_name="Yossi",
                    last_name="Cohen", email="cmt-a1@alpha.example", country_code="IL",
                    status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1), birth_date=date(1990, 1, 1)))
    db.add(TaxRulePack(pack_id="CMT-PACK-A", country_code="IL",
                       grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                       effective_start_date=date(2000, 1, 1), calculation_method="FLAT_RATE",
                       official_source_url=SRC))
    db.flush()
    db.add(TaxRatesHistory(country_code="IL", grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                           effective_start_date=date(2000, 1, 1), capital_gains_rate=0.25,
                           official_source_url=SRC, pack_id="CMT-PACK-A"))
    db.add(OptionPool(pool_id="CMT-POOL-A", company_id="CMT-COMP-A", total_shares=10000.0,
                      allocated_shares=0.0, unallocated_shares=10000.0))
    db.add(Trustee(trustee_id="CMT-TRUST-A", company_id="CMT-COMP-A", name="Trustee Ltd",
                   registration_number="123"))
    db.flush()

    record_ownership(db, aggregate_id="CMT-POOL-A", aggregate_type="OptionPool", company_id="CMT-COMP-A")
    append_event(db, event_type="POOL_BALANCE_ESTABLISHED", aggregate_type="OptionPool",
                aggregate_id="CMT-POOL-A",
                payload={"allocated_shares": 0.0, "unallocated_shares": 10000.0, "total_shares": 10000.0},
                effective_date=date(2020, 1, 1))
    db.commit()

    admin_a = _user(db, "CMT-U-ADMIN-A", UserRole.COMPANY_ADMIN, company_id="CMT-COMP-A")
    admin_b = _user(db, "CMT-U-ADMIN-B", UserRole.COMPANY_ADMIN, company_id="CMT-COMP-B")
    emp_a = _user(db, "CMT-U-EMP-A1", UserRole.EMPLOYEE, employee_id="CMT-EMP-A1")
    from types import SimpleNamespace
    return SimpleNamespace(db=db, admin_a=_token(db, admin_a), admin_b=_token(db, admin_b),
                           emp_a=_token(db, emp_a))


def _export_bundle(client, headers) -> dict:
    run_id = client.post(f"{API}/admin/export", headers=headers).json()["run_id"]
    return client.get(f"{API}/admin/export/{run_id}/download", headers=headers).json()


def _grant_and_approve(client, world) -> dict:
    """מייצר מענק+מסמך+בקשת מימוש מאושרת אמיתיים בחברה A - כדי שהבאנדל
    שמיוצא ממנה יכלול grants/vesting_schedules/documents/exercise_requests/
    exercise_tax_records/ledger_events/audit_log אמיתיים, כולל actor_user_id/
    created_by_user_id/reviewed_by_user_id שכן מאוכלסים (לא None) - זה בדיוק
    מה שמאפשר לבדוק שה-commit מאפס אותם ביעד."""
    world.db.query(Employee).filter(Employee.employee_id == "CMT-EMP-A1").update({"birth_date": date(1990, 1, 1)})
    world.db.commit()
    grant_resp = client.post(f"{API}/admin/grants", headers=world.admin_a, json={
        "employee_id": "CMT-EMP-A1", "pool_id": "CMT-POOL-A",
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
    return {"grant_id": grant_id, "request_id": request_id}


def _full_synthetic_bundle() -> dict:
    """באנדל עם IDs טריים לגמרי (לא נגזרים משום חברה שכבר קיימת ב-DB הזה).

    ייבוא-בין-חברות אמיתי הוא ייבוא בין שתי מערכות נפרדות - היעד לעולם לא
    מחזיק כבר את המפתחות הראשיים של המקור. שימוש בבאנדל שיוצא מ-company A
    בתוך אותו DB ואז מיובא ל-B תמיד יתנגש (decision 9, אותה בדיקה בדיוק
    כמו test_cross_company_id_collision_is_rejected_not_upserted) - זו לא
    תקלה, זו בדיוק ההגנה. לכן בדיקת "כתיבה מוצלחת בין חברות" חייבת מפתחות
    סינתטיים, לא באנדל שיוצא בפועל מחברה חיה באותו DB."""
    return _bundle_shape(
        "CMT-SYN-SRC",
        employees=[{"employee_id": "CMT-SYN-EMP", "company_id": "CMT-SYN-SRC", "first_name": "Syn",
                   "last_name": "Thetic", "email": "syn@x.example", "country_code": "IL",
                   "status": "ACTIVE", "hire_date": "2020-01-01", "termination_date": None,
                   "birth_date": "1990-01-01", "national_id": None}],
        option_pools=[{"pool_id": "CMT-SYN-POOL", "company_id": "CMT-SYN-SRC", "total_shares": 1000.0,
                      "allocated_shares": 100.0, "unallocated_shares": 900.0, "created_at": None}],
        trustees=[{"trustee_id": "CMT-SYN-TRUSTEE", "company_id": "CMT-SYN-SRC", "name": "Syn Trustee",
                  "registration_number": "999"}],
        grants=[{"grant_id": "CMT-SYN-GRANT", "employee_id": "CMT-SYN-EMP", "pool_id": "CMT-SYN-POOL",
                "trustee_id": "CMT-SYN-TRUSTEE", "grant_date": "2024-01-01",
                "grant_type": "IL_102_CAPITAL_GAINS", "total_options": 100.0, "exercise_price": 1.0,
                "currency": "USD", "trustee_deposit_date": "2024-01-01", "post_termination_window_days": 90}],
        vesting_schedules=[{"schedule_id": "CMT-SYN-SCHED", "grant_id": "CMT-SYN-GRANT",
                           "start_date": "2024-01-01", "cliff_months": 12, "total_months": 48,
                           "paused_days_total": 0}],
        documents=[{"document_id": "CMT-SYN-DOC", "template_type": "GRANT_LETTER", "grant_id": "CMT-SYN-GRANT",
                   "company_id": "CMT-SYN-SRC", "employee_id": "CMT-SYN-EMP", "trustee_id": "CMT-SYN-TRUSTEE",
                   "status": "SENT", "version": 1, "is_latest": True, "file_path": "syn/doc.pdf",
                   "file_sha256": "deadbeef", "generated_at": "2024-01-02T00:00:00+00:00",
                   "sent_at": "2024-01-02T00:00:00+00:00", "expires_at": None, "acknowledged_at": None,
                   "acknowledged_by_user_id": None, "created_by_user_id": "FOREIGN-ADMIN-NOT-IMPORTED"}],
        exercise_requests=[{"request_id": "CMT-SYN-REQ", "grant_id": "CMT-SYN-GRANT",
                           "employee_id": "CMT-SYN-EMP", "options_requested": 10.0,
                           "requested_at": "2024-06-01T00:00:00+00:00", "status": "APPROVED",
                           "reviewed_by_user_id": "FOREIGN-ADMIN-NOT-IMPORTED",
                           "reviewed_at": "2024-06-02T00:00:00+00:00", "review_notes": None}],
        exercise_tax_records=[{"record_id": "CMT-SYN-TAXREC", "request_id": "CMT-SYN-REQ",
                              "country_code": "IL", "grant_type": "IL_102_CAPITAL_GAINS",
                              "effective_start_date": "2000-01-01", "calculation_method": "FLAT_RATE",
                              "gain": 10.0, "tax_amount": 2.5, "effective_rate": 0.25,
                              "official_source_url": SRC, "computed_at": "2024-06-02T00:00:00+00:00"}],
        tax_rule_packs=[{"country_code": "IL", "grant_type": "IL_102_CAPITAL_GAINS",
                        "effective_start_date": "2000-01-01", "calculation_method": "FLAT_RATE",
                        "official_source_url": SRC, "created_at": None}],
        tax_rates_history=[{"tax_rule_id": "irrelevant", "country_code": "IL",
                           "grant_type": "IL_102_CAPITAL_GAINS", "effective_start_date": "2000-01-01",
                           "capital_gains_rate": 0.25, "official_source_url": SRC}],
        ledger_events=[
            {"event_id": "CMT-SYN-EVT-POOL", "event_type": "POOL_BALANCE_ESTABLISHED",
            "aggregate_type": "OptionPool", "aggregate_id": "CMT-SYN-POOL",
            "payload": json.dumps({"allocated_shares": 100.0, "unallocated_shares": 900.0, "total_shares": 1000.0}),
            "effective_date": "2020-01-01", "recorded_at": "2020-01-01T00:00:00+00:00",
            "actor_user_id": None, "sequence_no": 1, "corrects_event_id": None,
            "schema_version": 1, "source": "BACKFILL_v0.6.0"},
            {"event_id": "CMT-SYN-EVT-EMP", "event_type": "EMPLOYEE_STATE_ESTABLISHED",
            "aggregate_type": "Employee", "aggregate_id": "CMT-SYN-EMP",
            "payload": json.dumps({"status": "ACTIVE", "termination_date": None}),
            "effective_date": "2020-01-01", "recorded_at": "2020-01-01T00:00:00+00:00",
            "actor_user_id": None, "sequence_no": 1, "corrects_event_id": None,
            "schema_version": 1, "source": "BACKFILL_v0.6.0"},
            {"event_id": "CMT-SYN-EVT-GRANT", "event_type": "GRANT_CREATED",
            "aggregate_type": "Grant", "aggregate_id": "CMT-SYN-GRANT",
            "payload": json.dumps({"employee_id": "CMT-SYN-EMP", "pool_id": "CMT-SYN-POOL"}),
            "effective_date": "2024-01-01", "recorded_at": "2024-01-01T00:00:00+00:00",
            "actor_user_id": "FOREIGN-ADMIN-NOT-IMPORTED", "sequence_no": 1, "corrects_event_id": None,
            "schema_version": 1, "source": "LIVE"},
            {"event_id": "CMT-SYN-EVT-SCHED", "event_type": "VESTING_SCHEDULE_ESTABLISHED",
            "aggregate_type": "VestingSchedule", "aggregate_id": "CMT-SYN-SCHED",
            "payload": json.dumps({"start_date": "2024-01-01", "cliff_months": 12, "total_months": 48,
                                  "paused_days_total": 0}),
            "effective_date": "2024-01-01", "recorded_at": "2024-01-01T00:00:00+00:00",
            "actor_user_id": "FOREIGN-ADMIN-NOT-IMPORTED", "sequence_no": 1, "corrects_event_id": None,
            "schema_version": 1, "source": "LIVE"},
            {"event_id": "CMT-SYN-EVT-REQ", "event_type": "EXERCISE_REQUEST_SUBMITTED",
            "aggregate_type": "ExerciseRequest", "aggregate_id": "CMT-SYN-REQ",
            "payload": json.dumps({"options_requested": 10.0, "grant_id": "CMT-SYN-GRANT"}),
            "effective_date": "2024-06-01", "recorded_at": "2024-06-01T00:00:00+00:00",
            "actor_user_id": "FOREIGN-EMPLOYEE-NOT-IMPORTED", "sequence_no": 1, "corrects_event_id": None,
            "schema_version": 1, "source": "LIVE"},
        ],
        audit_log=[{"audit_id": "CMT-SYN-AUDIT", "entity_type": "Grant", "entity_id": "CMT-SYN-GRANT",
                   "action": "CREATED", "actor_user_id": "FOREIGN-ADMIN-NOT-IMPORTED",
                   "occurred_at": "2024-01-01T00:00:00+00:00", "before_value": None,
                   "after_value": None, "notes": None}],
    )


def _bundle_shape(company_id: str, **table_overrides) -> dict:
    empty_tables = ["employees", "option_pools", "trustees", "grants", "vesting_schedules",
                    "documents", "exercise_requests", "exercise_tax_records", "ledger_events",
                    "audit_log", "notification_preferences", "notification_dismissals",
                    "tax_rule_packs", "tax_rates_history", "income_tax_brackets"]
    tables = {name: [] for name in empty_tables}
    tables.update(table_overrides)
    return {"export_schema_version": EXPORT_SCHEMA_VERSION, "company_id": company_id,
           "contains_demo_tax_data": False,
           "tables": {"companies": [{"company_id": company_id, "name": "Placeholder",
                                     "country_code": "IL", "is_active": True, "created_at": None}],
                     **tables}}


# ===================================================================
# כתיבה בפועל, וסירוב מלא כשה-dry_run הפנימי לא תקין (all-or-nothing)
# ===================================================================

def test_commit_writes_a_full_synthetic_bundle_into_a_fresh_company_and_nulls_user_references(db_session):
    """מפתחות סינתטיים לגמרי (ראו _full_synthetic_bundle) - לא באנדל שיוצא
    מחברה חיה באותו DB, כדי שהבדיקה תבחן ייבוא-בין-חברות *מוצלח* ולא
    התנגשות (זו כבר מכוסה ע"י test_cross_company_id_collision_...)."""
    db = db_session
    db.add(Company(company_id="CMT-COMP-TARGET-FULL", name="Target", country_code="IL"))
    db.commit()

    bundle = _full_synthetic_bundle()
    report = import_service.commit(db, bundle, "CMT-COMP-TARGET-FULL")
    assert report.valid is True
    assert report.rows_failed == 0
    assert report.rows_written > 0

    employee = db.query(Employee).filter(Employee.employee_id == "CMT-SYN-EMP").one()
    assert employee.company_id == "CMT-COMP-TARGET-FULL", "company_id נאכף מהשרת (decision 9), לא מהקובץ"
    grant = db.query(Grant).filter(Grant.grant_id == "CMT-SYN-GRANT").one()
    assert grant.pool_id == "CMT-SYN-POOL", "מפתחות ראשיים נשמרים כמו שהם (הבהרה מ-task #6)"
    assert db.query(VestingSchedule).filter(VestingSchedule.grant_id == "CMT-SYN-GRANT").one()
    request = db.query(ExerciseRequest).filter(ExerciseRequest.request_id == "CMT-SYN-REQ").one()
    assert request.reviewed_by_user_id is None, "reviewed_by_user_id מפנה למשתמש שמעולם לא יובא - חייב להתאפס"
    assert db.query(ExerciseTaxRecord).filter(ExerciseTaxRecord.request_id == "CMT-SYN-REQ").one()

    doc = db.query(Document).filter(Document.document_id == "CMT-SYN-DOC").one()
    assert doc.company_id == "CMT-COMP-TARGET-FULL"
    assert doc.created_by_user_id is None
    assert doc.acknowledged_by_user_id is None

    ledger_rows = db.query(LedgerEvent).filter(LedgerEvent.aggregate_id == "CMT-SYN-GRANT").all()
    assert ledger_rows, "ledger_events של המענק אמורים להיכתב"
    assert all(e.actor_user_id is None for e in ledger_rows)

    audit = db.query(AuditLog).filter(AuditLog.audit_id == "CMT-SYN-AUDIT").one()
    assert audit.actor_user_id is None

    pool = db.query(OptionPool).filter(OptionPool.pool_id == "CMT-SYN-POOL").one()
    assert pool.company_id == "CMT-COMP-TARGET-FULL"
    assert pool.allocated_shares == 100.0
    assert pool.unallocated_shares == 900.0


def test_commit_makes_zero_writes_when_the_rerun_dry_run_is_invalid(client, world):
    """A מנסה "לייבא" חבילה שבה employee_id זהה כבר קיים תחת B - בדיוק
    כמו test_cross_company_id_collision_is_rejected_not_upserted, אבל כאן
    בודקים את commit(): all-or-nothing, לא כתיבה חלקית של שאר השורות התקינות."""
    bundle = _export_bundle(client, world.admin_a)
    employees_before = world.db.query(Employee).count()
    pools_before = world.db.query(OptionPool).count()

    report = import_service.commit(world.db, bundle, "CMT-COMP-B")
    assert report.valid is False
    assert report.rows_written == 0
    assert report.rows_failed > 0

    assert world.db.query(Employee).count() == employees_before
    assert world.db.query(OptionPool).count() == pools_before


def test_reimporting_your_own_export_into_yourself_writes_nothing(client, world):
    _grant_and_approve(client, world)
    bundle = _export_bundle(client, world.admin_a)

    report = import_service.commit(world.db, bundle, "CMT-COMP-A")
    assert report.valid is True
    assert report.rows_written == 0, "הכל כבר שייך ל-A - הכל אמור להיות SKIP_EXISTING, כלום לא נכתב"
    assert report.rows_skipped_existing == report.rows_attempted


def test_recommitting_the_same_bundle_does_not_duplicate_ledger_events(db_session):
    db = db_session
    db.add(Company(company_id="CMT-COMP-TARGET-RECOMMIT", name="Target", country_code="IL"))
    db.commit()
    bundle = _full_synthetic_bundle()

    first = import_service.commit(db, bundle, "CMT-COMP-TARGET-RECOMMIT")
    assert first.valid is True
    assert first.rows_written > 0
    ledger_count_after_first = db.query(LedgerEvent).count()

    second = import_service.commit(db, bundle, "CMT-COMP-TARGET-RECOMMIT")
    assert second.valid is True
    assert second.rows_written == 0, "כל השורות כבר קיימות מהניסיון הקודם - אידמפוטנטי"
    assert db.query(LedgerEvent).count() == ledger_count_after_first


# ===================================================================
# פרויקציית OptionPool - חוזרת ומחושבת אחרי הבאטש, לא לפני (§3)
# ===================================================================

def test_option_pool_balance_is_recomputed_after_new_events_land_on_an_existing_pool(db_session):
    db = db_session
    db.add(Company(company_id="CMT-POOL-TARGET", name="Target", country_code="IL"))
    db.add(OptionPool(pool_id="CMT-POOL-SHARED", company_id="CMT-POOL-TARGET",
                      total_shares=1000.0, allocated_shares=200.0, unallocated_shares=800.0))
    db.flush()
    record_ownership(db, aggregate_id="CMT-POOL-SHARED", aggregate_type="OptionPool",
                     company_id="CMT-POOL-TARGET")
    append_event(db, event_type="POOL_BALANCE_ESTABLISHED", aggregate_type="OptionPool",
                aggregate_id="CMT-POOL-SHARED",
                payload={"allocated_shares": 0.0, "unallocated_shares": 1000.0, "total_shares": 1000.0},
                effective_date=date(2020, 1, 1))
    append_event(db, event_type="POOL_ALLOCATED", aggregate_type="OptionPool",
                aggregate_id="CMT-POOL-SHARED", payload={"amount": 200.0},
                effective_date=date(2021, 1, 1))
    db.commit()

    # השורה עצמה SKIP_EXISTING (pool_id כבר קיים ב-B) - הערכים בקובץ (500/500)
    # לא אמורים לדרוס את מה שכבר ב-DB (decision D). האירוע החדש (sequence_no=3,
    # שלא קיים עדיין) הוא NEW.
    bundle = _bundle_shape(
        "CMT-SRC-IRRELEVANT",
        option_pools=[{"pool_id": "CMT-POOL-SHARED", "company_id": "CMT-SRC-IRRELEVANT",
                      "total_shares": 1000.0, "allocated_shares": 500.0, "unallocated_shares": 500.0,
                      "created_at": None}],
        ledger_events=[{"event_id": "CMT-EVT-NEW-1", "event_type": "POOL_ALLOCATED",
                       "aggregate_type": "OptionPool", "aggregate_id": "CMT-POOL-SHARED",
                       "payload": json.dumps({"amount": 300.0}), "effective_date": "2024-06-01",
                       "recorded_at": "2024-06-01T00:00:00+00:00", "actor_user_id": "FOREIGN-USER-NOT-IMPORTED",
                       "sequence_no": 3, "corrects_event_id": None, "schema_version": 1, "source": "LIVE"}],
    )

    report = import_service.commit(db, bundle, "CMT-POOL-TARGET")
    assert report.valid is True
    assert report.rows_written == 1, "רק אירוע ה-ledger החדש נכתב - הפול עצמו SKIP_EXISTING"

    pool = db.get(OptionPool, "CMT-POOL-SHARED")
    assert pool.allocated_shares == 500.0, "200 (קיים) + 300 (חדש) - קופל מחדש מה-ledger, לא הועתק מהקובץ"
    assert pool.unallocated_shares == 500.0
    new_event = db.query(LedgerEvent).filter(LedgerEvent.event_id == "CMT-EVT-NEW-1").one()
    assert new_event.actor_user_id is None


# ===================================================================
# חבילות מס - pack_id מתחדש ביעד, מותאם מחדש לפי natural key (decision 1)
# ===================================================================

def test_tax_rates_history_pack_id_is_resolved_by_natural_key_not_copied_from_file(db_session):
    db = db_session
    db.add(Company(company_id="CMT-TAX-TARGET", name="TaxTarget", country_code="IL"))
    db.commit()

    bundle = _bundle_shape(
        "CMT-TAX-SRC",
        tax_rule_packs=[{"country_code": "IL", "grant_type": "IL_102_WORK_INCOME",
                        "effective_start_date": "2010-01-01", "calculation_method": "FLAT_RATE",
                        "official_source_url": SRC, "created_at": None}],
        tax_rates_history=[{"tax_rule_id": "irrelevant-source-id", "country_code": "IL",
                           "grant_type": "IL_102_WORK_INCOME", "effective_start_date": "2010-01-01",
                           "capital_gains_rate": 0.4, "official_source_url": SRC}],
    )
    assert "pack_id" not in bundle["tables"]["tax_rule_packs"][0]
    assert "pack_id" not in bundle["tables"]["tax_rates_history"][0]

    report = import_service.commit(db, bundle, "CMT-TAX-TARGET")
    assert report.valid is True

    pack = db.query(TaxRulePack).filter(TaxRulePack.country_code == "IL",
                                        TaxRulePack.grant_type == "IL_102_WORK_INCOME",
                                        TaxRulePack.effective_start_date == date(2010, 1, 1)).one()
    rate = db.query(TaxRatesHistory).filter(TaxRatesHistory.country_code == "IL",
                                            TaxRatesHistory.grant_type == "IL_102_WORK_INCOME",
                                            TaxRatesHistory.effective_start_date == date(2010, 1, 1)).one()
    assert rate.pack_id == pack.pack_id
    assert rate.pack_id != "irrelevant-source-id"


# ===================================================================
# notification_preferences/notification_dismissals - לעולם לא נכתבות
# ===================================================================

def test_new_notification_preference_is_reported_not_portable_and_never_written(db_session):
    db = db_session
    db.add(Company(company_id="CMT-NOTIF-TARGET", name="NotifTarget", country_code="IL"))
    db.commit()

    bundle = _bundle_shape(
        "CMT-NOTIF-SRC",
        notification_preferences=[{"preference_id": "CMT-NP-1", "user_id": "FOREIGN-USER-NEVER-IMPORTED",
                                  "rule": "VESTING_EVENT_NEAR", "enabled": True, "lead_days": 14}],
    )

    dry = import_service.dry_run(db, bundle, "CMT-NOTIF-TARGET")
    assert dry.valid is True
    assert dry.rows_not_portable == 1
    assert dry.rows_new == 0

    report = import_service.commit(db, bundle, "CMT-NOTIF-TARGET")
    assert report.valid is True
    assert report.rows_not_portable == 1
    assert report.rows_written == 0
    assert db.query(NotificationPreference).filter(
        NotificationPreference.preference_id == "CMT-NP-1").first() is None
