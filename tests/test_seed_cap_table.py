"""B10 (docs/plan/v1.2.0.md §6) - הזרעת טבלת הון ב-``backend/seed_data.py``.

מוכיחה את קריטריון קבלה 13: ``python -m backend.seed_data`` מייצר טבלת הון
שאינה ריקה, **ואירוע ``SHARE_ISSUANCE_ESTABLISHED`` לכל הנפקה**.

הבדיקה מריצה את ``seed_cap_table`` עצמה - הפונקציה שהזרעה המלאה קוראת לה -
ולא את ``seed_database`` כולו: הזרעה מלאה כוללת ``drop_all`` + ``alembic
upgrade head`` + 260 עובדים, כלומר עשרות שניות על כל הרצת סוויטה, והיא לא
הייתה מוכיחה דבר נוסף על טבלת ההון. הפער היחיד שנשאר - "האם ההזרעה בכלל
קוראת לפונקציה" - נסגר בבדיקה נפרדת למטה ולא בהנחה.
"""

from datetime import date

import pytest

from backend.app.models import (
    Company, Employee, EmployeeStatus, LedgerEvent, LedgerOwnership,
    LEDGER_SOURCE_BACKFILL, OptionPool, ShareClass, ShareIssuance, Shareholder,
)
from backend.app.services.cap_table import compute_cap_table_snapshot
from backend.app.types import business_today
from backend.seed_data import (
    CAP_TABLE_SHARE_CLASSES, COMP_001_HOLDERS, COMP_002_HOLDERS, seed_cap_table,
)

# הכמויות נגזרות מהדאטה הזרוע עצמו ולא נכתבות ביד: מספר קשיח כאן היה נשבר בכל
# פעם שמישהו מוסיף בעל מניות, וזו בדיוק הבדיקה שהופכת אז לרעש.
COMP_001_ISSUANCE_COUNT = sum(len(issuances) for *_, issuances in COMP_001_HOLDERS)
COMP_001_TOTAL_SHARES = float(sum(shares for *_, issuances in COMP_001_HOLDERS
                                  for _, shares, _ in issuances))
COMP_001_AUTHORIZED = 10_000_000.0


@pytest.fixture
def seeded(db_session):
    """שתי החברות שההזרעה האמיתית מזריעה להן טבלת הון, ושני העובדים שהיא
    מקשרת אליהם - ``Shareholder.employee_id`` הוא FK אמיתי."""
    db = db_session
    db.add_all([
        Company(company_id="COMP-001", name="ESOP Tech Ltd", country_code="IL"),
        Company(company_id="COMP-002", name="Meridian US Inc", country_code="US"),
    ])
    for emp_id in ("EMP-001", "EMP-TAX-WORKINCOME-1"):
        db.add(Employee(employee_id=emp_id, company_id="COMP-001", first_name="עובד",
                        last_name=emp_id, email=f"{emp_id.lower()}@seed.example",
                        country_code="IL", status=EmployeeStatus.ACTIVE,
                        hire_date=date(2021, 1, 1)))
    db.add(OptionPool(pool_id="POOL-2021", company_id="COMP-001", total_shares=1_000_000.0,
                      allocated_shares=0.0, unallocated_shares=1_000_000.0))
    db.flush()

    today = business_today()
    seed_cap_table(db, "COMP-001", today, COMP_001_AUTHORIZED, COMP_001_HOLDERS)
    seed_cap_table(db, "COMP-002", today, 2_000_000.0, COMP_002_HOLDERS)
    db.flush()
    return db


def test_seed_cap_table_creates_a_non_empty_cap_table(seeded):
    """ק"ק 13, חצי ראשון: הטבלה אינה ריקה, והמזהים דטרמיניסטיים."""
    db = seeded
    assert db.query(ShareClass).filter(ShareClass.company_id == "COMP-001").count() == \
        len(CAP_TABLE_SHARE_CLASSES)
    assert db.query(Shareholder).filter(Shareholder.company_id == "COMP-001").count() == \
        len(COMP_001_HOLDERS)
    assert db.query(ShareIssuance).filter(ShareIssuance.company_id == "COMP-001").count() == \
        COMP_001_ISSUANCE_COUNT
    # מזהה יציב ולא UUID - זו התכונה שמאפשרת לתעד תוצאה מצופה ב-docs/qa/
    # ולבנות את ה-DB מחדש בלי שכל הפניה תישבר (ראו §7 במפרט).
    assert db.get(Shareholder, "SH-COMP-001-FOUNDER-1") is not None
    assert db.get(ShareIssuance, "SI-COMP-001-INVESTOR-SEED-2") is not None


def test_every_seeded_issuance_gets_exactly_one_established_event(seeded):
    """ק"ק 13, חצי שני. *לכל* הנפקה, ובדיוק אחד - שני אירועי בסיס לאותה מנה
    היו נספרים פעמיים ב-replay."""
    db = seeded
    issuance_ids = [row.share_issuance_id for row in db.query(ShareIssuance).all()]
    assert issuance_ids

    for issuance_id in issuance_ids:
        events = (db.query(LedgerEvent)
                  .filter(LedgerEvent.aggregate_id == issuance_id).all())
        assert [e.event_type for e in events] == ["SHARE_ISSUANCE_ESTABLISHED"], issuance_id
        assert events[0].aggregate_type == "ShareIssuance"
        # effective_date הוא תאריך ההנפקה האמיתי, לא LEDGER_EPOCH ולא היום -
        # בלי זה תמונת מצב לפי as_of היסטורי מחזירה כמות שגויה.
        assert events[0].effective_date == db.get(ShareIssuance, issuance_id).issue_date
        assert db.get(LedgerOwnership, issuance_id) is not None


def test_seeded_events_are_not_marked_as_backfill(seeded):
    """``backfill_ledger.main`` מסרב לרוץ אם קיים ולו אירוע גיבוי אחד. אירוע
    הזרעה שסומן BACKFILL היה חוסם בשקט את גיבוי כל שאר הישויות בצעד 13."""
    db = seeded
    sources = {row.source for row in db.query(LedgerEvent).all()}
    assert LEDGER_SOURCE_BACKFILL not in sources


def test_seeded_cap_table_snapshot_is_complete_and_not_partial(seeded):
    """ההזרעה חייבת לייצר טבלת הון ש*החישוב* מרוצה ממנה: partial=True או אזהרה
    כאן פירושם שורת הנפקה בלי אירוע - בדיוק המצב שהאירוע נכתב כדי למנוע."""
    db = seeded
    snapshot = compute_cap_table_snapshot(db, "COMP-001")

    assert snapshot["partial"] is False
    assert snapshot["warnings"] == []
    assert snapshot["outstanding_shares"] == COMP_001_TOTAL_SHARES
    # total_authorized_shares נזרע גם הוא - בלעדיו שני האחוזים הם None (דפוס
    # הכשל P4), כלומר מסך הדילול מוזרע ריק מהמספר שהוא נועד להראות.
    assert snapshot["total_authorized_shares"] == COMP_001_AUTHORIZED
    assert snapshot["outstanding_pct_of_authorized"] == pytest.approx(
        COMP_001_TOTAL_SHARES / COMP_001_AUTHORIZED)
    # שתי המנות של אותו משקיע ובאותו סוג מניה מתאחדות לשורת פילוח אחת.
    seed_key = ("SH-COMP-001-INVESTOR-SEED", "SC-COMP-001-PREF-A")
    rows = {(r["shareholder_id"], r["share_class_id"]): r["shares"]
            for r in snapshot["by_shareholder_and_class"]}
    assert rows[seed_key] == 1_200_000.0


def test_second_company_cap_table_is_isolated(seeded):
    """בידוד טננטים על דאטה זרועה: לשתי החברות יש טבלת הון, ואף שורה של
    COMP-002 אינה נספרת ל-COMP-001."""
    db = seeded
    snapshot_002 = compute_cap_table_snapshot(db, "COMP-002")
    assert snapshot_002["outstanding_shares"] == 1_100_000.0
    assert snapshot_002["partial"] is False

    comp_002_ids = {row.share_issuance_id for row in db.query(ShareIssuance)
                    .filter(ShareIssuance.company_id == "COMP-002").all()}
    comp_001_ids = {row.share_issuance_id for row in db.query(ShareIssuance)
                    .filter(ShareIssuance.company_id == "COMP-001").all()}
    assert comp_002_ids and not (comp_002_ids & comp_001_ids)


def test_seed_cap_table_refuses_to_exceed_authorized_shares(db_session):
    """ההזרעה אוכפת את אותה תקרה שהאנדפוינט אוכף. בלי זה אפשר לזרוע DB שהקוד
    עצמו היה דוחה, וזה מתגלה רק בהזנה הבאה של האדמין."""
    db = db_session
    db.add(Company(company_id="COMP-TIGHT", name="Tight", country_code="IL"))
    db.flush()

    with pytest.raises(RuntimeError, match="מעל התקרה"):
        seed_cap_table(db, "COMP-TIGHT", business_today(), 1_000.0,
                       [("FOUNDER-1", "Too Big", "FOUNDER", None, [("COMMON", 5_000, 12)])])


def test_seed_database_actually_calls_seed_cap_table():
    """הפער היחיד שבדיקה על הפונקציה לבדה משאירה: פונקציה נכונה שאיש אינו
    קורא לה. נסגר בקריאת המקור ולא בהרצת ההזרעה המלאה (drop_all + מיגרציות)."""
    import inspect

    import backend.seed_data as seed_data

    source = inspect.getsource(seed_data.seed_database)
    assert "seed_cap_table(db, \"COMP-001\"" in source
    assert "seed_cap_table(db, \"COMP-002\"" in source
