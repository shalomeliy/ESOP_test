"""חד-פעמי: מגבה (backfill) את המצב הקיים ל-ledger_events + ledger_ownership.

מריצים פעם אחת, אחרי המיגרציה שיוצרת את שתי הטבלאות, ולפני שכל endpoint חי
מתחיל לכתוב אירועים (שלב 3). לא עושה drop_all/create_all כמו seed_data.py -
זה סקריפט תוספתי על סכמה קיימת עם דאטה אמיתי, לא בנייה מאפס.

*** לא לרוץ פעמיים על אותו DB *** - הסקריפט בודק זאת ומסרב אם כבר יש אירועי
גיבוי, כדי לא להכפיל אותם.

כל אירוע מגיבוי הוא אירוע "בסיס" (ESTABLISHED, או GRANT_CREATED/*_SUBMITTED
עבור הישויות שאין להן היסטוריית ביניים לשחזר) - התאריך האפקטיבי נלקח מהשדה
ההיסטורי הקיים הכי משמעותי (grant_date, hire_date/termination_date וכו'),
אבל תאריך הידיעה (recorded_at) הוא רגע הרצת הגיבוי עצמו, לא מזויף לאחור.
ראו הדיון המלא ב-FEATURE_SPEC.md סעיף v0.6.0 ו-QA_TESTBOOK.md R-060.
"""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from backend.app.database import SessionLocal
import backend.app.models as models
from backend.app.services.ledger import append_event, record_ownership
from backend.app.models import LEDGER_SOURCE_BACKFILL, LedgerEvent


def _company_id_of_pool(db, pool_id: str) -> str:
    pool = db.query(models.OptionPool).filter(models.OptionPool.pool_id == pool_id).first()
    return pool.company_id if pool else None


def backfill(db, run_at: datetime) -> dict:
    counts = {"pools": 0, "employees": 0, "grants": 0, "deposits": 0,
              "schedules": 0, "requests": 0, "decisions": 0}

    for pool in db.query(models.OptionPool).all():
        record_ownership(db, aggregate_id=pool.pool_id, aggregate_type="OptionPool",
                         company_id=pool.company_id)
        append_event(
            db, event_type="POOL_BALANCE_ESTABLISHED", aggregate_type="OptionPool",
            aggregate_id=pool.pool_id,
            payload={"allocated_shares": pool.allocated_shares,
                    "unallocated_shares": pool.unallocated_shares,
                    "total_shares": pool.total_shares},
            effective_date=(pool.created_at or run_at).date(),
            recorded_at=run_at, source=LEDGER_SOURCE_BACKFILL,
        )
        counts["pools"] += 1

    for emp in db.query(models.Employee).all():
        record_ownership(db, aggregate_id=emp.employee_id, aggregate_type="Employee",
                         company_id=emp.company_id, employee_id=emp.employee_id)
        # תאריך אפקטיבי: אם עזב/נפטר - תאריך העזיבה הוא העובדה המשמעותית האחרונה
        # שידועה; אחרת תאריך הגיוס. שני המקרים הם "האמת ההיסטורית הכי טובה שיש".
        effective = emp.termination_date or emp.hire_date
        append_event(
            db, event_type="EMPLOYEE_STATE_ESTABLISHED", aggregate_type="Employee",
            aggregate_id=emp.employee_id,
            payload={"status": emp.status.value if hasattr(emp.status, "value") else emp.status,
                    "termination_date": emp.termination_date},
            effective_date=effective, recorded_at=run_at, source=LEDGER_SOURCE_BACKFILL,
        )
        counts["employees"] += 1

    for grant in db.query(models.Grant).all():
        company_id = _company_id_of_pool(db, grant.pool_id)
        record_ownership(db, aggregate_id=grant.grant_id, aggregate_type="Grant",
                         company_id=company_id, trustee_id=grant.trustee_id,
                         employee_id=grant.employee_id)
        append_event(
            db, event_type="GRANT_CREATED", aggregate_type="Grant",
            aggregate_id=grant.grant_id,
            payload={"employee_id": grant.employee_id, "pool_id": grant.pool_id,
                    "trustee_id": grant.trustee_id,
                    "grant_type": grant.grant_type.value if hasattr(grant.grant_type, "value") else grant.grant_type,
                    "total_options": grant.total_options, "exercise_price": grant.exercise_price,
                    "currency": grant.currency,
                    "post_termination_window_days": grant.post_termination_window_days,
                    "trustee_deposit_date": None},
            effective_date=grant.grant_date, recorded_at=run_at, source=LEDGER_SOURCE_BACKFILL,
        )
        counts["grants"] += 1

        if grant.trustee_deposit_date:
            append_event(
                db, event_type="TRUSTEE_DEPOSIT_CONFIRMED", aggregate_type="Grant",
                aggregate_id=grant.grant_id,
                payload={"deposit_date": grant.trustee_deposit_date},
                effective_date=grant.trustee_deposit_date, recorded_at=run_at,
                source=LEDGER_SOURCE_BACKFILL,
            )
            counts["deposits"] += 1

        schedule = grant.vesting_schedule
        if schedule:
            record_ownership(db, aggregate_id=schedule.schedule_id, aggregate_type="VestingSchedule",
                             company_id=company_id, trustee_id=grant.trustee_id,
                             employee_id=grant.employee_id)
            append_event(
                db, event_type="VESTING_SCHEDULE_ESTABLISHED", aggregate_type="VestingSchedule",
                aggregate_id=schedule.schedule_id,
                payload={"start_date": schedule.start_date, "cliff_months": schedule.cliff_months,
                        "total_months": schedule.total_months,
                        "paused_days_total": schedule.paused_days_total},
                effective_date=schedule.start_date, recorded_at=run_at, source=LEDGER_SOURCE_BACKFILL,
            )
            counts["schedules"] += 1

    for req in db.query(models.ExerciseRequest).all():
        company_id = _company_id_of_pool(
            db, db.query(models.Grant.pool_id).filter(models.Grant.grant_id == req.grant_id).scalar())
        record_ownership(db, aggregate_id=req.request_id, aggregate_type="ExerciseRequest",
                         company_id=company_id, employee_id=req.employee_id)
        requested_on = req.requested_at.date() if req.requested_at else run_at.date()
        append_event(
            db, event_type="EXERCISE_REQUEST_SUBMITTED", aggregate_type="ExerciseRequest",
            aggregate_id=req.request_id,
            payload={"options_requested": req.options_requested, "grant_id": req.grant_id},
            effective_date=requested_on, recorded_at=run_at, source=LEDGER_SOURCE_BACKFILL,
        )
        counts["requests"] += 1

        status_value = req.status.value if hasattr(req.status, "value") else req.status
        if status_value in ("APPROVED", "REJECTED"):
            decided_on = req.reviewed_at.date() if req.reviewed_at else requested_on
            append_event(
                db, event_type="EXERCISE_REQUEST_DECIDED", aggregate_type="ExerciseRequest",
                aggregate_id=req.request_id,
                payload={"status": status_value, "notes": req.review_notes},
                effective_date=decided_on, recorded_at=run_at, source=LEDGER_SOURCE_BACKFILL,
            )
            counts["decisions"] += 1

    return counts


def main():
    db = SessionLocal()
    try:
        already_ran = db.query(LedgerEvent).filter(LedgerEvent.source == LEDGER_SOURCE_BACKFILL).first()
        if already_ran:
            print("⛔ כבר קיימים אירועי גיבוי ב-DB הזה - לא רץ שוב (מונע כפילויות).")
            return

        run_at = datetime.utcnow()
        print(f"🧾 מתחיל גיבוי ל-ledger, recorded_at={run_at.isoformat()}...")
        counts = backfill(db, run_at)
        db.commit()
        print(f"✅ הושלם: {counts}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
