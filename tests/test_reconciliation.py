"""התאמת ייבוא - שירות בלבד (v0.9.1 שלב ב, PLAN.md §8 שלב 9).

reconcile() מריץ מחדש calculate_vested_options ו-calculate_tax על תוכן
החבילה מול מה שהתקבל ביעד אחרי commit() (task #7) - לא רק ספירת שורות.
הבדיקה המרכזית בקובץ הזה היא "שורת יעד מזויפת" (tampered-target-row): אם
ההתאמה הייתה משווה שורת-יעד לעצמה (טאוטולוגיה) היא הייתה ירוקה תמיד; כאן
אנחנו מוכיחים שהיא קוראת בפועל מה-DB ומזהה סטייה אמיתית.
"""

from datetime import date

import pytest

from backend.app.models import (
    Company, ExerciseRequest, ExerciseTaxRecord, Grant, GrantType, OptionPool,
    TaxRatesHistory, TaxRulePack, VestingSchedule,
)
from backend.app.services import import_ as import_service
from backend.app.services import reconciliation as reconciliation_service
from backend.app.services.export import EXPORT_SCHEMA_VERSION

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
    from backend.app.models import Employee, EmployeeStatus
    db.add(Employee(employee_id="RCN-SYN-EMP", company_id="RCN-TARGET", first_name="Syn",
                    last_name="Thetic", email="rcn-syn@x.example", country_code="IL",
                    status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1), birth_date=date(1990, 1, 1)))
    db.commit()
    return db


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
