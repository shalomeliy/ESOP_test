"""SearchEngine - fuzzy matching *וגם* גבולות הרשאה.

הסיכון הכפול: (1) חיפוש שלא סובל שגיאת הקלדה הוא חסר ערך; (2) חיפוש שסובלני
מדי ולא מוגבל לחברה הופך לדלף מידע בין חברות - בדיוק מה ש-list_employees עושה
במכוון עד שתוקן (QA-050-20 ב-QA_TESTBOOK.md), ומה ש-SearchEngine מצהיר שהוא *לא* עושה.
"""

from datetime import date

import pytest

from backend.app.models import (Company, Employee, EmployeeStatus, Grant, GrantType,
                                OptionPool)
from backend.app.services.search_engine import SearchEngine, _MIN_SCORE


@pytest.fixture
def two_companies(db_session):
    """שתי חברות נפרדות לחלוטין, עם שמות משפחה שלא דומים זה לזה,
    כדי שכל דלף בין החברות יהיה חד-משמעי ולא "אולי fuzzy match"."""
    db_session.add_all([
        Company(company_id="COMP-A", name="Alpha Systems", country_code="IL"),
        Company(company_id="COMP-B", name="Beta Works", country_code="IL"),
    ])
    db_session.add_all([
        OptionPool(pool_id="POOL-A", company_id="COMP-A", total_shares=100000.0,
                   allocated_shares=0.0, unallocated_shares=100000.0),
        OptionPool(pool_id="POOL-B", company_id="COMP-B", total_shares=100000.0,
                   allocated_shares=0.0, unallocated_shares=100000.0),
    ])
    db_session.add_all([
        Employee(employee_id="EMP-A1", company_id="COMP-A", first_name="Yossi",
                 last_name="Cohen", email="yossi.cohen@alpha.example", country_code="IL",
                 status=EmployeeStatus.ACTIVE, hire_date=date(2021, 1, 1)),
        # שם המשפחה 'Katz' נבחר במכוון: _score('Zilberstein', ...) עליו = 0.235,
        # מתחת לסף 0.35. הבחירה הראשונה, 'Peretz', קיבלה 0.353 - מעל הסף - ולכן
        # הופיעה כתוצאה ולהפוך את בדיקת הדלף לדו-משמעית. ראו הערת ה-precision
        # בתחתית הקובץ.
        Employee(employee_id="EMP-A2", company_id="COMP-A", first_name="Dana",
                 last_name="Katz", email="dana.katz@alpha.example", country_code="IL",
                 status=EmployeeStatus.ACTIVE, hire_date=date(2021, 1, 1)),
        Employee(employee_id="EMP-B1", company_id="COMP-B", first_name="Rivka",
                 last_name="Zilberstein", email="rivka.zilberstein@beta.example",
                 country_code="IL", status=EmployeeStatus.ACTIVE, hire_date=date(2021, 1, 1)),
    ])
    db_session.add_all([
        Grant(grant_id="GRANT-A1", employee_id="EMP-A1", pool_id="POOL-A",
              grant_date=date(2022, 1, 1), grant_type=GrantType.IL_102_CAPITAL_GAINS,
              total_options=1000.0, exercise_price=1.0, post_termination_window_days=90),
        Grant(grant_id="GRANT-A2", employee_id="EMP-A2", pool_id="POOL-A",
              grant_date=date(2022, 1, 1), grant_type=GrantType.IL_102_CAPITAL_GAINS,
              total_options=2000.0, exercise_price=1.0, post_termination_window_days=90),
        Grant(grant_id="GRANT-B1", employee_id="EMP-B1", pool_id="POOL-B",
              grant_date=date(2022, 1, 1), grant_type=GrantType.IL_102_CAPITAL_GAINS,
              total_options=3000.0, exercise_price=1.0, post_termination_window_days=90),
    ])
    db_session.flush()
    return db_session


def test_admin_finds_own_employee_despite_typo(two_companies):
    """שם משפחה 'Cohen' מוקלד 'Cohan' - שגיאת אות אחת.
    difflib.SequenceMatcher('cohan','cohen') = 2*4/(5+5) = 0.8, הרבה מעל סף
    _MIN_SCORE=0.35, ולכן העובד הנכון חייב להיות התוצאה הראשונה.
    """
    results = SearchEngine.search_for_admin(two_companies, "COMP-A", "Cohan")

    assert results, "חיפוש מוטעה באות אחת החזיר אפס תוצאות - החיפוש הפך לחסר ערך"
    top = results[0]
    assert top.entity_type == "Employee"
    assert top.entity_id == "EMP-A1"
    assert top.score >= _MIN_SCORE
    assert top.score >= 0.35


def test_admin_cannot_reach_another_companys_employee_by_exact_name(two_companies):
    """אדמין של COMP-A מחפש את שם המשפחה המדויק של עובדת COMP-B.
    התאמה מושלמת מבחינת טקסט - ולכן זו בדיוק הבדיקה שתופסת דלף בין-חברתי:
    אם ההגבלה על company_id תיפול, התוצאה תחזור בניקוד ~1.0.
    """
    results = SearchEngine.search_for_admin(two_companies, "COMP-A", "Zilberstein")

    employee_hits = [r for r in results if r.entity_type == "Employee"]
    assert employee_hits == [], (
        "דלף בין חברות: אדמין של COMP-A קיבל עובד/ים מ-COMP-B: "
        f"{[r.entity_id for r in employee_hits]}"
    )
    # וגם שום ישות אחרת של COMP-B לא הגיעה דרך אותו חיפוש.
    assert all(not r.entity_id.endswith("-B1") for r in results)


def test_admin_of_company_b_does_find_her(two_companies):
    """בקרה חיובית לבדיקה הקודמת: העובדת אכן קיימת וניתנת למציאה - ע"י
    האדמין הנכון. בלי זה, בדיקת האפס למעלה יכולה לעבור מסיבה לא נכונה."""
    results = SearchEngine.search_for_admin(two_companies, "COMP-B", "Zilberstein")
    assert any(r.entity_type == "Employee" and r.entity_id == "EMP-B1" for r in results)


def test_employee_search_returns_only_their_own_grants(two_companies):
    """עובד מחפש 'GRANT' - מחרוזת שמופיעה בכל מזהי המענקים במערכת.
    מותר לו לראות רק את GRANT-A1 שלו; GRANT-A2 (עמית באותה חברה) ו-GRANT-B1
    (חברה אחרת) חייבים להיעדר.
    """
    results = SearchEngine.search_for_employee(two_companies, "EMP-A1", "GRANT")

    grant_ids = {r.entity_id for r in results if r.entity_type == "Grant"}
    assert grant_ids == {"GRANT-A1"}, f"עובד ראה מענקים שאינם שלו: {grant_ids}"


# ---------------------------------------------------------------------------
# הערת דיוק (precision) שנמדדה בזמן כתיבת הבדיקות, לא מכוסה בבדיקה מכוונת:
# _MIN_SCORE=0.35 נמוך מספיק כדי ששאילתה של 11 תווים ('Zilberstein') תעבור את
# הסף מול הכתובת 'dana.peretz@alpha.example' בניקוד 0.353 - false positive
# אמיתי, לא דלף הרשאות. הבדיקות כאן לא נועלות את הסף (הוא כוונון מוצר), אבל
# הנתון מתועד כאן כדי שלא ייעלם. ראו דוח ה-QA של v0.4.0.
# ---------------------------------------------------------------------------
