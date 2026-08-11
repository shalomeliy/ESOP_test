"""התאמת ייבוא (v0.9.1 שלב ב, PLAN.md §8 שלבים 9-10).

reconcile() מריץ מחדש calculate_vested_options ו-calculate_tax על תוכן
החבילה מול מה שהתקבל ביעד אחרי commit() (task #7) - לא רק ספירת שורות.
הבדיקה המרכזית בחלק ה-service-level היא "שורת יעד מזויפת" (tampered-
target-row): אם ההתאמה הייתה משווה שורת-יעד לעצמה (טאוטולוגיה) היא הייתה
ירוקה תמיד; כאן אנחנו מוכיחים שהיא קוראת בפועל מה-DB ומזהה סטייה אמיתית.

שני חלקי הקובץ, אותה חלוקה בדיוק כמו test_import_commit.py: HTTP-level
(שני ה-endpoints של task #10 - היסטוריה ודוח התאמה) למעלה, service-level
(reconciliation_service.reconcile ישירות, task #9) למטה.
"""

from datetime import date, timedelta

import pytest

from backend.app.auth import hash_password
from backend.app.models import (
    Company, DataTransferRun, Employee, EmployeeStatus, ExerciseRequest, ExerciseTaxRecord, Grant,
    GrantType, OptionPool, TaxRatesHistory, TaxRulePack, User, UserRole, UserSession, VestingSchedule,
)
from backend.app.services import import_ as import_service
from backend.app.services import reconciliation as reconciliation_service
from backend.app.services.export import EXPORT_SCHEMA_VERSION
from backend.app.types import utcnow

API = "/api/v1"
SRC = "https://test.invalid/qa-fixture-not-a-real-tax-source"


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


def _reconciliation_bundle() -> dict:
    """מענק+לוח הבשלה+בקשת מימוש+רשומת מס אחת, מפתחות סינתטיים לגמרי (לא
    נגזרים מחברה שכבר קיימת ב-DB הזה) - אותו נימוק בדיוק כמו
    test_import_commit.py::_full_synthetic_bundle."""
    return _bundle_shape(
        "RCN-SYN-SRC",
        grants=[{"grant_id": "RCN-SYN-GRANT", "employee_id": "RCN-SYN-EMP", "pool_id": "RCN-SYN-POOL",
                "trustee_id": None, "grant_date": "2024-01-01",
                "grant_type": "IL_102_CAPITAL_GAINS", "total_options": 100.0, "exercise_price": 1.0,
                "currency": "USD", "trustee_deposit_date": "2024-01-01", "post_termination_window_days": 90}],
        vesting_schedules=[{"schedule_id": "RCN-SYN-SCHED", "grant_id": "RCN-SYN-GRANT",
                           "start_date": "2024-01-01", "cliff_months": 12, "total_months": 48,
                           "paused_days_total": 0}],
        exercise_requests=[{"request_id": "RCN-SYN-REQ", "grant_id": "RCN-SYN-GRANT",
                           "employee_id": "RCN-SYN-EMP", "options_requested": 10.0,
                           "requested_at": "2024-06-01T00:00:00+00:00", "status": "APPROVED",
                           "reviewed_by_user_id": None, "reviewed_at": "2024-06-02T00:00:00+00:00",
                           "review_notes": None}],
        exercise_tax_records=[{"record_id": "RCN-SYN-TAXREC", "request_id": "RCN-SYN-REQ",
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
    )


# vesting_schedules/exercise_requests/exercise_tax_records כולן מפנות ל-
# grant/employee שאינם בתוך ה-bundle עצמו (employees/option_pools ריקים
# בכוונה) - הן דורשות שהיעד כבר מכיל אותם, בדיוק כמו fixture אחר בקובץ.
@pytest.fixture
def target(db_session):
    db = db_session
    db.add(Company(company_id="RCN-TARGET", name="Target", country_code="IL"))
    db.add(OptionPool(pool_id="RCN-SYN-POOL", company_id="RCN-TARGET", total_shares=1000.0,
                      allocated_shares=0.0, unallocated_shares=1000.0))
    db.commit()
    db.add(Employee(employee_id="RCN-SYN-EMP", company_id="RCN-TARGET", first_name="Syn",
                    last_name="Thetic", email="rcn-syn@x.example", country_code="IL",
                    status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1), birth_date=date(1990, 1, 1)))
    db.commit()
    return db


# ===================================================================
# HTTP-level (task #10) - שני ה-endpoints מעל reconcile(): היסטוריה ודוח
# התאמה. אותו דפוס בדיוק כמו test_import_commit.py - client/admin tokens
# אמיתיים, לא קריאה ישירה לשירות.
# ===================================================================

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
    """חברת יעד (RCE-TARGET, עם employee/pool תואמים ל-_reconciliation_bundle)
    וחברה שלישית לא-קשורה (RCE-OTHER) - לבדיקת 403 חוצה-חברות על ה-run_id
    של ה-commit."""
    db = db_session
    db.add_all([
        Company(company_id="RCE-TARGET", name="Target", country_code="IL"),
        Company(company_id="RCE-OTHER", name="Other", country_code="IL"),
    ])
    db.add(OptionPool(pool_id="RCN-SYN-POOL", company_id="RCE-TARGET", total_shares=1000.0,
                      allocated_shares=0.0, unallocated_shares=1000.0))
    db.commit()
    db.add(Employee(employee_id="RCN-SYN-EMP", company_id="RCE-TARGET", first_name="Syn",
                    last_name="Thetic", email="rce-syn@x.example", country_code="IL",
                    status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1), birth_date=date(1990, 1, 1)))
    db.commit()

    admin_target = _user(db, "RCE-U-ADMIN-TARGET", UserRole.COMPANY_ADMIN, company_id="RCE-TARGET")
    admin_other = _user(db, "RCE-U-ADMIN-OTHER", UserRole.COMPANY_ADMIN, company_id="RCE-OTHER")
    from types import SimpleNamespace
    return SimpleNamespace(db=db, admin_target=_token(db, admin_target), admin_other=_token(db, admin_other))


def _upload_dry_run(client, headers, bundle: dict):
    import json
    return client.post(f"{API}/admin/import/dry-run", headers=headers,
                       files={"file": ("export.json", json.dumps(bundle).encode("utf-8"),
                                       "application/json")})


def _commit(client, headers, dry_run_id: str):
    return client.post(f"{API}/admin/import/commit", headers=headers, json={"dry_run_id": dry_run_id})


def test_reconciliation_endpoint_returns_a_clean_report_after_a_successful_commit(client, world):
    dry_resp = _upload_dry_run(client, world.admin_target, _reconciliation_bundle())
    assert dry_resp.status_code == 200, dry_resp.text
    commit_resp = _commit(client, world.admin_target, dry_resp.json()["run_id"])
    assert commit_resp.status_code == 200, commit_resp.text
    commit_run_id = commit_resp.json()["run_id"]

    response = client.get(f"{API}/admin/export-import/{commit_run_id}/reconciliation",
                          headers=world.admin_target)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["clean"] is True
    assert body["mismatches"] == []
    assert body["grants_checked"] == 1
    assert body["exercises_checked"] == 1
    assert body["known_limitations"], "known_limitations לא אמור להיות ריק אף פעם"


def test_reconciliation_endpoint_rejects_an_unknown_run_id(client, world):
    response = client.get(f"{API}/admin/export-import/NO-SUCH-RUN/reconciliation",
                          headers=world.admin_target)
    assert response.status_code == 404


def test_reconciliation_endpoint_rejects_a_run_that_was_never_committed(client, world):
    """דריי-ראן (IMPORT_DRY_RUN) אינו IMPORT_COMMIT - אין מה להתאים נגדו,
    בדיוק כמו run_id שלא קיים בכלל."""
    dry_resp = _upload_dry_run(client, world.admin_target, _reconciliation_bundle())
    dry_run_id = dry_resp.json()["run_id"]

    response = client.get(f"{API}/admin/export-import/{dry_run_id}/reconciliation",
                          headers=world.admin_target)
    assert response.status_code == 404


def test_reconciliation_endpoint_rejects_a_commit_belonging_to_another_company(client, world):
    dry_resp = _upload_dry_run(client, world.admin_target, _reconciliation_bundle())
    commit_resp = _commit(client, world.admin_target, dry_resp.json()["run_id"])
    commit_run_id = commit_resp.json()["run_id"]

    response = client.get(f"{API}/admin/export-import/{commit_run_id}/reconciliation",
                          headers=world.admin_other)
    assert response.status_code == 403


def test_history_endpoint_lists_runs_scoped_to_the_callers_company(client, world):
    dry_resp = _upload_dry_run(client, world.admin_target, _reconciliation_bundle())
    _commit(client, world.admin_target, dry_resp.json()["run_id"])

    response = client.get(f"{API}/admin/export-import/history", headers=world.admin_target)
    assert response.status_code == 200
    directions = {row["direction"] for row in response.json()}
    assert directions == {"IMPORT_DRY_RUN", "IMPORT_COMMIT"}

    other_response = client.get(f"{API}/admin/export-import/history", headers=world.admin_other)
    assert other_response.json() == [], "לחברה אחרת אין שום run - לא אמורה לראות את אלה של היעד"


def test_reconciliation_endpoint_returns_500_when_the_bundle_file_is_missing_from_disk(client, world):
    """file_path עדיין מאוכלס בעמודה, אבל הקובץ עצמו נמחק מהדיסק (ניקוי,
    שחזור חלקי) - נבדל מ-run שלא קיים בכלל (404): זה כשל אחסון בשרת (500),
    בדיוק כמו download_export. נמצא בסקירה עצמאית של task #10: הניסוח
    הקודם כאן טען 404 בלי לבדוק את הדיסק בפועל, וקרס ב-500 לא-מטופל."""
    import os

    from backend.app.services.export import EXPORT_STORE_DIR

    dry_resp = _upload_dry_run(client, world.admin_target, _reconciliation_bundle())
    dry_run_id = dry_resp.json()["run_id"]
    commit_resp = _commit(client, world.admin_target, dry_run_id)
    commit_run_id = commit_resp.json()["run_id"]

    file_path = world.db.query(DataTransferRun.file_path).filter(
        DataTransferRun.run_id == dry_run_id).scalar()
    os.remove(EXPORT_STORE_DIR / file_path)

    response = client.get(f"{API}/admin/export-import/{commit_run_id}/reconciliation",
                          headers=world.admin_target)
    assert response.status_code == 500


def test_reconciliation_endpoint_surfaces_a_real_mismatch_in_the_response_body(client, world):
    """כל שאר בדיקות ה-HTTP בקובץ הזה בודקות רק את המסלול הנקי - הבדיקה הזו
    מוודאת שדוח לא-נקי מגיע בפועל בגוף התשובה עם הפרטים הנכונים, לא רק
    שהשירות (reconcile() ישירות) מזהה את זה בבדיקות ה-service-level למטה."""
    dry_resp = _upload_dry_run(client, world.admin_target, _reconciliation_bundle())
    commit_resp = _commit(client, world.admin_target, dry_resp.json()["run_id"])
    commit_run_id = commit_resp.json()["run_id"]

    rate_row = world.db.query(TaxRatesHistory).filter(
        TaxRatesHistory.country_code == "IL", TaxRatesHistory.grant_type == "IL_102_CAPITAL_GAINS",
    ).one()
    rate_row.capital_gains_rate = 0.40
    world.db.commit()

    response = client.get(f"{API}/admin/export-import/{commit_run_id}/reconciliation",
                          headers=world.admin_target)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["clean"] is False
    mismatch = next(m for m in body["mismatches"] if m["entity_type"] == "ExerciseTaxRecord")
    assert mismatch["entity_id"] == "RCN-SYN-TAXREC"
    assert mismatch["source_value"] == 2.5
    assert mismatch["target_value"] == 4.0


def test_history_endpoint_includes_export_runs_scoped_to_the_callers_company(client, world):
    """גם EXPORT (source_company_id, target_company_id ריק) חייב להישאר
    בהיקף הנכון - הבדיקה הקודמת בקובץ הזה בדקה רק IMPORT_DRY_RUN/
    IMPORT_COMMIT (target_company_id בלבד)."""
    export_resp = client.post(f"{API}/admin/export", headers=world.admin_target)
    assert export_resp.status_code == 200, export_resp.text

    own = client.get(f"{API}/admin/export-import/history", headers=world.admin_target)
    assert "EXPORT" in {row["direction"] for row in own.json()}

    other = client.get(f"{API}/admin/export-import/history", headers=world.admin_other)
    assert other.json() == [], "לחברה אחרת אין שום run - לא אמורה לראות את הייצוא של היעד"


def test_history_endpoint_rejects_an_unknown_direction_filter(client, world):
    response = client.get(f"{API}/admin/export-import/history?direction=NOT_A_REAL_DIRECTION",
                          headers=world.admin_target)
    assert response.status_code == 400


def test_history_endpoint_filters_by_direction(client, world):
    dry_resp = _upload_dry_run(client, world.admin_target, _reconciliation_bundle())
    _commit(client, world.admin_target, dry_resp.json()["run_id"])

    response = client.get(f"{API}/admin/export-import/history?direction=IMPORT_COMMIT",
                          headers=world.admin_target)
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["direction"] == "IMPORT_COMMIT"


def _commit_reconciliation_bundle(db) -> dict:
    bundle = _reconciliation_bundle()
    report = import_service.commit(db, bundle, "RCN-TARGET")
    assert report.valid is True, report.errors
    db.commit()
    return bundle


# ===================================================================
# הבשלה
# ===================================================================

def test_reconciliation_recomputes_vested_options_and_flags_a_mismatch(target):
    """שורת-יעד מזויפת: מתעדים הבשלה נקייה, ואז משנים ידנית את לוח ההבשלה
    *ביעד* (אחרי ה-commit) - אם ההתאמה הייתה משווה שורת-יעד לעצמה, השינוי
    הזה לא היה נתפס אף פעם."""
    db = target
    bundle = _commit_reconciliation_bundle(db)
    as_of = date(2026, 1, 1)  # 24 חודשים אחרי תחילת ההבשלה, לפני תום 48 החודשים

    clean_report = reconciliation_service.reconcile(db, bundle, as_of=as_of)
    assert clean_report.clean, clean_report.mismatches

    schedule = db.query(VestingSchedule).filter(VestingSchedule.grant_id == "RCN-SYN-GRANT").one()
    schedule.cliff_months = 36  # אחרי as_of - ה-cliff לא הגיע עדיין ביעד
    db.commit()

    tampered_report = reconciliation_service.reconcile(db, bundle, as_of=as_of)
    assert not tampered_report.clean
    mismatch = tampered_report.mismatches[0]
    assert mismatch.entity_type == "Grant"
    assert mismatch.entity_id == "RCN-SYN-GRANT"
    assert mismatch.field_name == "vested_options"
    assert mismatch.source_value == 50.0  # (100/48)*24
    assert mismatch.target_value == 0.0  # cliff המזויף (36) עדיין לא הגיע


def test_reconciliation_does_not_flag_a_grant_with_no_vesting_schedule_in_the_bundle(target):
    db = target
    bundle = _bundle_shape(
        "RCN-NOSCHED-SRC",
        grants=[{"grant_id": "RCN-NOSCHED-GRANT", "employee_id": "RCN-SYN-EMP", "pool_id": "RCN-SYN-POOL",
                "trustee_id": None, "grant_date": "2024-01-01", "grant_type": "IL_102_CAPITAL_GAINS",
                "total_options": 50.0, "exercise_price": 1.0, "currency": "USD",
                "trustee_deposit_date": "2024-01-01", "post_termination_window_days": 90}],
    )
    report = import_service.commit(db, bundle, "RCN-TARGET")
    assert report.valid is True, report.errors
    db.commit()

    result = reconciliation_service.reconcile(db, bundle, as_of=date(2026, 1, 1))
    assert result.clean
    assert result.grants_checked == 0, "אין לוח הבשלה בחבילה - אין מה להריץ מחדש, לא אי-התאמה"


def test_reconciliation_reports_a_missing_target_schedule_without_crashing(target):
    """מצב שלא אמור לקרות אחרי commit תקין (all-or-nothing) - נבדק בכל זאת
    כבדיקת חוסן, לא כתלות בבאג אמיתי. VestingSchedule (לא Grant): למענק אין
    שורות תלויות עליו, ולכן אפשר למחוק אותו בבידוד בלי להפר FK אחר
    (exercise_requests/exercise_tax_records עדיין מפנים ל-Grant עצמו)."""
    db = target
    bundle = _commit_reconciliation_bundle(db)
    db.query(VestingSchedule).filter(VestingSchedule.grant_id == "RCN-SYN-GRANT").delete()
    db.commit()

    result = reconciliation_service.reconcile(db, bundle, as_of=date(2026, 1, 1))
    assert not result.clean
    assert result.mismatches[0].entity_type == "Grant"


# ===================================================================
# מס
# ===================================================================

def test_reconciliation_replays_tax_calc_for_completed_exercises_using_the_new_record(target):
    db = target
    bundle = _commit_reconciliation_bundle(db)

    clean_report = reconciliation_service.reconcile(db, bundle, as_of=date(2026, 1, 1))
    assert clean_report.clean, clean_report.mismatches
    assert clean_report.exercises_checked == 1


def test_reconciliation_flags_a_tax_pack_that_resolves_differently_on_the_target(target):
    """לא רק tax_amount: משנים את השיעור על היעד כך שהחישוב מחדש עדיין
    "מוצא" חבילה (לא MissingTaxRuleError) אבל מגיע לתוצאה שונה - בדיוק
    הסיכון שמומחה המס העלה (חבילה "אחרת" שנפתרת בטעות בלי לקרוס)."""
    db = target
    bundle = _commit_reconciliation_bundle(db)

    rate_row = db.query(TaxRatesHistory).filter(
        TaxRatesHistory.country_code == "IL", TaxRatesHistory.grant_type == "IL_102_CAPITAL_GAINS",
    ).one()
    rate_row.capital_gains_rate = 0.40
    db.commit()

    report = reconciliation_service.reconcile(db, bundle, as_of=date(2026, 1, 1))
    assert not report.clean
    mismatch = next(m for m in report.mismatches if m.entity_type == "ExerciseTaxRecord")
    assert mismatch.entity_id == "RCN-SYN-TAXREC"
    assert mismatch.source_value == 2.5
    assert mismatch.target_value == 4.0  # 10.0 * 0.40


def test_reconciliation_reports_a_missing_target_exercise_request_without_crashing(target):
    """מצב שלא אמור לקרות אחרי commit תקין (all-or-nothing) - סימטרי לבדיקת
    ה-VestingSchedule החסר, בצד המס."""
    db = target
    bundle = _commit_reconciliation_bundle(db)
    # מוחקים את שתיהן, לפי סדר ה-FK (ExerciseTaxRecord.request_id -> exercise_requests) -
    # reconcile() לעולם לא קורא את ExerciseTaxRecord של היעד (רק את בשל ה-bundle),
    # אז מחיקתה כאן היא רק כדי לא להפר את האילוץ בעת מחיקת הבקשה עצמה.
    db.query(ExerciseTaxRecord).filter(ExerciseTaxRecord.record_id == "RCN-SYN-TAXREC").delete()
    db.query(ExerciseRequest).filter(ExerciseRequest.request_id == "RCN-SYN-REQ").delete()
    db.commit()

    report = reconciliation_service.reconcile(db, bundle, as_of=date(2026, 1, 1))
    assert not report.clean
    mismatch = next(m for m in report.mismatches if m.entity_type == "ExerciseTaxRecord")
    assert mismatch.entity_id == "RCN-SYN-TAXREC"
    assert "חסר ביעד" in mismatch.reason


def test_reconciliation_flags_a_missing_tax_rule_pack_on_the_target(target):
    db = target
    bundle = _commit_reconciliation_bundle(db)

    db.query(TaxRatesHistory).filter(TaxRatesHistory.country_code == "IL").delete()
    db.query(TaxRulePack).filter(TaxRulePack.country_code == "IL").delete()
    db.commit()

    report = reconciliation_service.reconcile(db, bundle, as_of=date(2026, 1, 1))
    assert not report.clean
    mismatch = next(m for m in report.mismatches if m.entity_type == "ExerciseTaxRecord")
    assert mismatch.target_value is None
    assert "לא נפתרת" in mismatch.reason


# ===================================================================
# דוח נקי + known_limitations
# ===================================================================

def test_reconciliation_clean_import_reports_zero_mismatches(target):
    db = target
    bundle = _commit_reconciliation_bundle(db)

    report = reconciliation_service.reconcile(db, bundle, as_of=date(2026, 1, 1))
    assert report.clean
    assert report.mismatches == []
    assert report.grants_checked == 1
    assert report.exercises_checked == 1


def test_reconciliation_report_always_states_its_known_limitations(target):
    db = target
    bundle = _commit_reconciliation_bundle(db)

    report = reconciliation_service.reconcile(db, bundle, as_of=date(2026, 1, 1))
    assert report.known_limitations, "דוח בלי הגבלות מוצהרות משדר ביטחון-יתר"
    assert any("SKIP_EXISTING" in note for note in report.known_limitations)
    assert any("2006" in note for note in report.known_limitations)
