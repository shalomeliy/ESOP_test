"""v0.6.0 שלב 1 - הקיפול (fold) וה-backfill.

הבדיקה החשובה ביותר כאן: replay מייצר בדיוק את אותו מצב שהעמודות המוטטות
מציגות היום. זו ההוכחה הקונקרטית לקריטריון 1 ב-GOAL.md (דטרמיניזם מוכח).
מיפוי ל-QA_TESTBOOK.md: QA-060-01 עד QA-060-10.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.app.types import utcnow
from backend.app.models import (
    Company, Employee, EmployeeStatus, ExerciseRequest, ExerciseRequestStatus,
    Grant, GrantType, LedgerEvent, LedgerOwnership, OptionPool, Trustee,
    VestingSchedule, LEDGER_SOURCE_BACKFILL, LEDGER_SOURCE_LIVE,
)
from backend.app.services.ledger import (
    UnknownLedgerAggregateType, UnknownLedgerEventType, append_event, events_for,
    project, project_employee, project_exercise_request, project_grant,
    project_option_pool, project_vesting_schedule, record_ownership,
)
from backend.backfill_ledger import backfill


# ===================================================================
# בדיקות יחידה על הקיפול - רשימות אירועים בנויות ביד, בלי DB
# ===================================================================

def _event(event_type, payload, effective_date, sequence_no=1, source=LEDGER_SOURCE_BACKFILL):
    return LedgerEvent(event_type=event_type, aggregate_type="X", aggregate_id="X",
                       payload=json.dumps(payload, default=str), effective_date=effective_date,
                       recorded_at=utcnow(), sequence_no=sequence_no, source=source)


def test_pool_projection_folds_established_then_deltas():
    events = [
        _event("POOL_BALANCE_ESTABLISHED",
              {"allocated_shares": 100.0, "unallocated_shares": 900.0, "total_shares": 1000.0},
              date(2026, 1, 1), sequence_no=1),
        _event("POOL_ALLOCATED", {"amount": 50.0}, date(2026, 2, 1), sequence_no=2),
        _event("POOL_UNVEST_RETURNED", {"amount": 20.0}, date(2026, 3, 1), sequence_no=3),
    ]
    state = project_option_pool(events)
    assert state == {"allocated_shares": 130.0, "unallocated_shares": 870.0, "total_shares": 1000.0}


def test_employee_projection_terminated_overrides_established():
    events = [
        _event("EMPLOYEE_STATE_ESTABLISHED", {"status": "ACTIVE", "termination_date": None},
              date(2020, 1, 1), sequence_no=1),
        _event("EMPLOYEE_STATUS_CHANGED", {"status": "TERMINATED", "termination_date": "2026-01-01"},
              date(2026, 1, 1), sequence_no=2),
    ]
    assert project_employee(events) == {"status": "TERMINATED", "termination_date": date(2026, 1, 1)}


def test_grant_projection_deposit_confirmed_after_creation():
    events = [
        _event("GRANT_CREATED", {"trustee_deposit_date": None}, date(2021, 1, 1), sequence_no=1),
        _event("TRUSTEE_DEPOSIT_CONFIRMED", {"deposit_date": "2021-02-01"},
              date(2021, 2, 1), sequence_no=2),
    ]
    assert project_grant(events) == {"trustee_deposit_date": date(2021, 2, 1)}


def test_grant_projection_without_deposit_stays_none():
    events = [_event("GRANT_CREATED", {"trustee_deposit_date": None}, date(2021, 1, 1))]
    assert project_grant(events) == {"trustee_deposit_date": None}


def test_exercise_request_projection_decided_overrides_submitted():
    events = [
        _event("EXERCISE_REQUEST_SUBMITTED", {"options_requested": 100.0}, date(2026, 1, 1), 1),
        _event("EXERCISE_REQUEST_DECIDED", {"status": "APPROVED"}, date(2026, 1, 5), 2),
    ]
    state = project_exercise_request(events)
    assert state["status"] == "APPROVED"


def test_vesting_schedule_projection_baseline_only():
    events = [_event("VESTING_SCHEDULE_ESTABLISHED",
                     {"start_date": "2022-01-01", "cliff_months": 12, "total_months": 48,
                      "paused_days_total": 30}, date(2022, 1, 1))]
    assert project_vesting_schedule(events) == {
        "start_date": date(2022, 1, 1), "cliff_months": 12, "total_months": 48, "paused_days_total": 30,
    }


def test_missing_aggregate_projects_to_none():
    """ישות שאין לה אירועים בכלל (למשל לפני שהגיבוי רץ) - None ולא קריסה. ראו
    R-060 ב-QA_TESTBOOK.md: שאילתה על תקופה שלפני הליבה חייבת לומר "אין נתון",
    לא להתחזות."""
    assert project_option_pool([]) is None


# ===================================================================
# append_event / record_ownership - ולידציה ורצף
# ===================================================================

def test_append_event_rejects_unknown_event_type(db_session):
    with pytest.raises(UnknownLedgerEventType):
        append_event(db_session, event_type="NOT_A_REAL_EVENT", aggregate_type="Grant",
                    aggregate_id="G-1", payload={}, effective_date=date.today())


def test_append_event_rejects_unknown_aggregate_type(db_session):
    with pytest.raises(UnknownLedgerAggregateType):
        append_event(db_session, event_type="GRANT_CREATED", aggregate_type="NotAThing",
                    aggregate_id="G-1", payload={}, effective_date=date.today())


def test_append_event_assigns_increasing_sequence_per_aggregate(db_session):
    e1 = append_event(db_session, event_type="GRANT_CREATED", aggregate_type="Grant",
                      aggregate_id="G-SEQ", payload={"trustee_deposit_date": None},
                      effective_date=date(2022, 1, 1))
    e2 = append_event(db_session, event_type="TRUSTEE_DEPOSIT_CONFIRMED", aggregate_type="Grant",
                      aggregate_id="G-SEQ", payload={"deposit_date": "2022-06-01"},
                      effective_date=date(2022, 6, 1))
    assert (e1.sequence_no, e2.sequence_no) == (1, 2)


def test_record_ownership_is_set_once_and_immutable(db_session):
    db_session.add(Company(company_id="C-1", name="Co", country_code="IL"))
    db_session.flush()
    first = record_ownership(db_session, aggregate_id="G-OWN", aggregate_type="Grant",
                             company_id="C-1")
    again = record_ownership(db_session, aggregate_id="G-OWN", aggregate_type="Grant",
                             company_id="SOMETHING-ELSE")
    assert again.company_id == "C-1", "רשומת בעלות קיימת נדרסה - היא אמורה להיות immutable"
    assert first.aggregate_id == again.aggregate_id


# ===================================================================
# הגנת שינוי - הטריגרים מה-migration אינם קיימים בסכמת ה-create_all של
# הבדיקות (create_all לא מריץ CREATE TRIGGER). נבנים כאן במפורש כדי שהבדיקה
# תרוץ נגד ההגנה האמיתית, לא נגד הנחה.
# ===================================================================

@pytest.fixture
def with_ledger_triggers(db_session):
    """יוצר את הטריגרים בתוך אותה טרנזקציה של הבדיקה, בלי commit(): מספיק
    ש-DDL ב-SQLite נכנס לתוקף באותו connection מיידית, וקריאה ל-commit() כאן
    הייתה סוגרת את הטרנזקציה החיצונית של conftest.py (ראו ההערה שם) - בדיוק
    המגבלה שגרמה לפיצול test_notifications.py לשתי בדיקות נפרדות."""
    db_session.execute(text("""
        CREATE TRIGGER IF NOT EXISTS trg_ledger_events_no_update BEFORE UPDATE ON ledger_events
        BEGIN SELECT RAISE(ABORT, 'ledger_events is append-only: UPDATE is rejected'); END
    """))
    db_session.execute(text("""
        CREATE TRIGGER IF NOT EXISTS trg_ledger_events_no_delete BEFORE DELETE ON ledger_events
        BEGIN SELECT RAISE(ABORT, 'ledger_events is append-only: DELETE is rejected'); END
    """))
    yield db_session
    # אין DROP TRIGGER בניקוי: ה-rollback של conftest.py על כל הטרנזקציה כבר
    # מבטל את ה-DDL הזה יחד עם כל שינוי אחר שהבדיקה עשתה.


def test_ledger_events_reject_update_at_the_db_level(with_ledger_triggers):
    db = with_ledger_triggers
    event = append_event(db, event_type="GRANT_CREATED", aggregate_type="Grant",
                         aggregate_id="G-TAMPER", payload={"trustee_deposit_date": None},
                         effective_date=date(2022, 1, 1))

    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError, match="append-only"):
        db.execute(text("UPDATE ledger_events SET source='TAMPERED' WHERE event_id=:id"),
                  {"id": event.event_id})


def test_ledger_events_reject_delete_at_the_db_level(with_ledger_triggers):
    db = with_ledger_triggers
    event = append_event(db, event_type="GRANT_CREATED", aggregate_type="Grant",
                         aggregate_id="G-TAMPER-2", payload={"trustee_deposit_date": None},
                         effective_date=date(2022, 1, 1))

    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError, match="append-only"):
        db.execute(text("DELETE FROM ledger_events WHERE event_id=:id"), {"id": event.event_id})


# ===================================================================
# QA-060-01: replay-equivalence - הבדיקה המרכזית של שלב 1.
# ===================================================================

@pytest.fixture
def seeded_world(db_session):
    """עולם קטן עם דוגמה מכל סוג ישות שהגיבוי צריך לטפל בה: פול, עובד פעיל,
    עובד שעזב, מענק עם הפקדת נאמן, מענק בלי לוח הבשלה, בקשת מימוש ממתינה
    ובקשה שאושרה - כדי שה-replay-equivalence תכסה את כל ה-event_type."""
    db = db_session
    db.add(Company(company_id="C-1", name="Alpha", country_code="IL"))
    db.add(OptionPool(pool_id="P-1", company_id="C-1", total_shares=10000.0,
                      allocated_shares=4800.0, unallocated_shares=5200.0,
                      created_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
    db.add(Trustee(trustee_id="T-1", company_id="C-1", name="Tr", registration_number="1"))
    db.add_all([
        Employee(employee_id="E-ACTIVE", company_id="C-1", first_name="A", last_name="B",
                email="a@x.example", country_code="IL", status=EmployeeStatus.ACTIVE,
                hire_date=date(2020, 1, 1)),
        Employee(employee_id="E-GONE", company_id="C-1", first_name="C", last_name="D",
                email="c@x.example", country_code="IL", status=EmployeeStatus.TERMINATED,
                hire_date=date(2019, 1, 1), termination_date=date(2024, 6, 1)),
    ])
    db.flush()  # עובדים חייבים להיות מוכנים לפני שמענקים מפנים אליהם
    db.add_all([
        Grant(grant_id="G-DEPOSIT", employee_id="E-ACTIVE", pool_id="P-1", trustee_id="T-1",
             grant_date=date(2021, 1, 1), grant_type=GrantType.IL_102_CAPITAL_GAINS,
             total_options=4800.0, exercise_price=1.0, post_termination_window_days=90,
             trustee_deposit_date=date(2021, 2, 1)),
        Grant(grant_id="G-NOSCHED", employee_id="E-GONE", pool_id="P-1",
             grant_date=date(2015, 1, 1), grant_type=GrantType.IL_102_CAPITAL_GAINS,
             total_options=1000.0, exercise_price=1.0, post_termination_window_days=90),
    ])
    # פלאש נפרד לפני VestingSchedule/ExerciseRequest: הכנסה של הרבה אובייקטים
    # תלויים בפלאש אחד לא תמיד ממוינת נכון כשאין relationship() מוצהר בין
    # Grant ל-ExerciseRequest (זו עובדה קיימת ב-models.py, לא באג כאן) -
    # נבדק בפועל: אותה קבוצת אובייקטים בפלאש אחד נכשלת ב-FOREIGN KEY constraint,
    # ובשני פלאשים עוברת.
    db.flush()
    db.add(VestingSchedule(schedule_id="S-1", grant_id="G-DEPOSIT", start_date=date(2021, 1, 1),
                           cliff_months=12, total_months=48, paused_days_total=15))
    db.add_all([
        ExerciseRequest(request_id="R-PENDING", grant_id="G-DEPOSIT", employee_id="E-ACTIVE",
                        options_requested=200.0, requested_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        status=ExerciseRequestStatus.PENDING),
        ExerciseRequest(request_id="R-APPROVED", grant_id="G-DEPOSIT", employee_id="E-ACTIVE",
                        options_requested=100.0, requested_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
                        status=ExerciseRequestStatus.APPROVED,
                        reviewed_at=datetime(2025, 12, 5, tzinfo=timezone.utc)),
    ])
    db.flush()
    return db


def test_replay_equivalence_for_every_aggregate_type(seeded_world):
    """QA-060-01. לכל ישות: הקיפול של האירועים שהגיבוי יצר == המצב הנוכחי
    בעמודות המוטטות בפועל. זו הבדיקה שהייתה תופסת כל נקודת מוטציה שנשכחה."""
    db = seeded_world
    run_at = utcnow()
    backfill(db, run_at)
    db.flush()

    pool = db.get(OptionPool, "P-1")
    assert project(db, "OptionPool", "P-1") == {
        "allocated_shares": pool.allocated_shares,
        "unallocated_shares": pool.unallocated_shares,
        "total_shares": pool.total_shares,
    }

    for emp_id in ("E-ACTIVE", "E-GONE"):
        emp = db.get(Employee, emp_id)
        assert project(db, "Employee", emp_id) == {
            "status": emp.status.value if hasattr(emp.status, "value") else emp.status,
            "termination_date": emp.termination_date,
        }

    for grant_id in ("G-DEPOSIT", "G-NOSCHED"):
        grant = db.get(Grant, grant_id)
        assert project(db, "Grant", grant_id) == {"trustee_deposit_date": grant.trustee_deposit_date}

    schedule = db.get(VestingSchedule, "S-1")
    assert project(db, "VestingSchedule", "S-1") == {
        "start_date": schedule.start_date, "cliff_months": schedule.cliff_months,
        "total_months": schedule.total_months, "paused_days_total": schedule.paused_days_total,
    }

    pending = project(db, "ExerciseRequest", "R-PENDING")
    approved = project(db, "ExerciseRequest", "R-APPROVED")
    assert pending["status"] == "PENDING"
    assert approved["status"] == "APPROVED"


def test_replay_equivalence_ownership_index_matches_real_scope(seeded_world):
    """מסכים חדשים מאשרים גישה מול ledger_ownership, לא מול דאטה משוחזר - זו
    ההגנה מפני IDOR שחוזר בצורה חדשה. הבדיקה מוודאת שהאינדקס בעצמו נכון."""
    db = seeded_world
    backfill(db, utcnow())
    db.flush()

    grant_ownership = db.get(LedgerOwnership, "G-DEPOSIT")
    assert (grant_ownership.company_id, grant_ownership.trustee_id, grant_ownership.employee_id) \
        == ("C-1", "T-1", "E-ACTIVE")

    pool_ownership = db.get(LedgerOwnership, "P-1")
    assert pool_ownership.company_id == "C-1"


def test_backfill_events_are_marked_as_backfill_source(seeded_world):
    """אירועי גיבוי חייבים להיות מובחנים בסכמה, לא בפרוזה - GOAL.md: אין
    מספר בלי שרשור מקורות."""
    db = seeded_world
    backfill(db, utcnow())
    db.flush()

    sources = {e.source for e in db.query(LedgerEvent).all()}
    assert sources == {LEDGER_SOURCE_BACKFILL}
    assert all(e.actor_user_id is None for e in db.query(LedgerEvent).all()), (
        "אירוע גיבוי לעולם לא משויך למשתמש אמיתי שלא ביצע את הפעולה בפועל"
    )


def test_query_before_backfill_knowledge_date_returns_no_history(seeded_world):
    """שאילתת 'מה חשבנו' עם תאריך ידיעה *לפני* רגע הגיבוי חייבת להחזיר 'אין
    נתון', לא להתחזות שהמערכת ידעה משהו לפני שהיא בכלל ידעה. QA-060-09."""
    db = seeded_world
    run_at = utcnow()
    backfill(db, run_at)
    db.flush()

    before_backfill = run_at - timedelta(days=1)
    assert project(db, "Grant", "G-DEPOSIT", as_of_knowledge_date=before_backfill) is None


def test_bitemporal_query_before_and_after_deposit_confirmation_differ():
    """QA-060-10, הדוגמה המחושבת ביד: הפקדת נאמן שנרשמה זמן קצר אחרי שהמענק
    נוצר. 'מה חשבנו לפני ההפקדה' ו'אחריה' הן שתי תשובות אמיתיות ושונות לאותה
    שאלה - זו ההדגמה הקונקרטית של קריטריון 3 ב-GOAL.md."""
    events = [
        _event("GRANT_CREATED", {"trustee_deposit_date": None}, date(2021, 1, 1), sequence_no=1),
    ]
    assert project_grant(events) == {"trustee_deposit_date": None}

    events.append(
        _event("TRUSTEE_DEPOSIT_CONFIRMED", {"deposit_date": "2021-01-20"},
              date(2021, 1, 20), sequence_no=2)
    )
    assert project_grant(events) == {"trustee_deposit_date": date(2021, 1, 20)}

    # ולפי effective_date בלבד (מה נכון בעולם), לא ידיעה: לפני 20/1 עדיין None.
    early = [e for e in events if e.effective_date <= date(2021, 1, 19)]
    assert project_grant(early) == {"trustee_deposit_date": None}
