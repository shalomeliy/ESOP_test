import sys
import calendar
from pathlib import Path
from datetime import date, timedelta

# הוספת נתיב השורש של הפרויקט ל-sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from backend.app.database import SessionLocal, engine, Base
import backend.app.models as models
from backend.app.auth import hash_password


def shift_months(d: date, months: int) -> date:
    """מזיז תאריך במספר חודשים (חיובי או שלילי), עם clamp ליום האחרון בחודש היעד
    (כדי לא להתרסק על תאריכים לא חוקיים כמו 31 בפברואר)."""
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def seed_database():
    print("🧹 מוחק טבלאות קיימות...")
    Base.metadata.drop_all(bind=engine)

    print("🏗️ בונה טבלאות מחדש...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    today = date.today()

    try:
        print("🌱 מתחיל הזרקת נתוני ניסיון (Seed Data)...")

        # -------------------------------------------------------------
        # 1. יצירת חברה (Company)
        # -------------------------------------------------------------
        company = models.Company(
            company_id="COMP-001",
            name="ESOP Tech Ltd",
            country_code="IL",
            is_active=True,
        )
        db.add(company)
        db.commit()

        # -------------------------------------------------------------
        # 2. יצירת פול אופציות (OptionPool) ונאמן (Trustee)
        # -------------------------------------------------------------
        pool = models.OptionPool(
            pool_id="POOL-2021",
            company_id=company.company_id,
            total_shares=1000000,
            allocated_shares=500000,
            unallocated_shares=500000
        )
        db.add(pool)

        trustee = models.Trustee(
            trustee_id="TRUSTEE-001",
            company_id=company.company_id,
            name="ESOP Trust Services",
            registration_number="515123456"
        )
        db.add(trustee)
        db.commit()

        # -------------------------------------------------------------
        # 3. יצירת עובדים (Employees) - שימוש ב-ACTIVE / TERMINATED בלבד
        # -------------------------------------------------------------
        emp1 = models.Employee(
            employee_id="EMP-001",
            company_id=company.company_id,
            first_name="ישראל",
            last_name="ישראלי",
            email="israel@company.com",
            country_code="IL",
            status=models.EmployeeStatus.ACTIVE,
            hire_date=date(2021, 1, 1)
        )

        emp_underage = models.Employee(
            employee_id="EMP-UNDERAGE-1",
            company_id=company.company_id,
            first_name="קטין",
            last_name="צעיר",
            email="minor@company.com",
            country_code="IL",
            status=models.EmployeeStatus.ACTIVE,
            hire_date=date(2025, 1, 1)
        )

        emp_retired = models.Employee(
            employee_id="EMP-RETIRED-1",
            company_id=company.company_id,
            first_name="משה",
            last_name="ותיק",
            email="retired@company.com",
            country_code="IL",
            status=models.EmployeeStatus.TERMINATED,
            hire_date=date(2015, 6, 1),
            termination_date=date(2022, 1, 1)
        )

        emp_dec_before = models.Employee(
            employee_id="EMP-DEC-BEFORE-1",
            company_id=company.company_id,
            first_name="דני",
            last_name="ז\"ל",
            email="dec_before@company.com",
            country_code="IL",
            status=models.EmployeeStatus.TERMINATED,
            hire_date=date(2020, 1, 1),
            termination_date=date(2021, 6, 1)
        )

        emp_dec_after = models.Employee(
            employee_id="EMP-DEC-AFTER-1",
            company_id=company.company_id,
            first_name="יוסי",
            last_name="ז\"ל",
            email="dec_after@company.com",
            country_code="IL",
            status=models.EmployeeStatus.TERMINATED,
            hire_date=date(2018, 1, 1),
            termination_date=date(2023, 1, 1)
        )

        emp_unexercised = models.Employee(
            employee_id="EMP-UNEXERCISED-1",
            company_id=company.company_id,
            first_name="אבי",
            last_name="לא-מימש",
            email="unex@company.com",
            country_code="IL",
            status=models.EmployeeStatus.TERMINATED,
            hire_date=date(2019, 2, 1),
            termination_date=date(2022, 5, 1)
        )

        db.add_all([emp1, emp_underage, emp_retired, emp_dec_before, emp_dec_after, emp_unexercised])
        db.commit()

        # -------------------------------------------------------------
        # 4. יצירת מענקים (Grants) - שימוש ב-IL_102_CAPITAL_GAINS
        # -------------------------------------------------------------
        g1 = models.Grant(
            grant_id="G-2021-001",
            employee_id=emp1.employee_id,
            pool_id=pool.pool_id,
            trustee_id=trustee.trustee_id,
            grant_date=date(2021, 1, 15),
            grant_type=models.GrantType.IL_102_CAPITAL_GAINS,
            total_options=10000,
            exercise_price=2.5,
            currency="ILS",
            trustee_deposit_date=date(2021, 2, 1)
        )

        g2 = models.Grant(
            grant_id="G-UNDERAGE-1",
            employee_id=emp_underage.employee_id,
            pool_id=pool.pool_id,
            trustee_id=trustee.trustee_id,
            grant_date=date(2025, 1, 15),
            grant_type=models.GrantType.IL_102_CAPITAL_GAINS,
            total_options=1000,
            exercise_price=1.0,
            currency="ILS",
            trustee_deposit_date=date(2025, 2, 1)
        )

        g3 = models.Grant(
            grant_id="G-DEC-BEFORE-1",
            employee_id=emp_dec_before.employee_id,
            pool_id=pool.pool_id,
            trustee_id=trustee.trustee_id,
            grant_date=date(2020, 1, 15),
            grant_type=models.GrantType.IL_102_CAPITAL_GAINS,
            total_options=5000,
            exercise_price=2.0,
            currency="ILS",
            trustee_deposit_date=date(2020, 2, 1)
        )

        g4 = models.Grant(
            grant_id="G-DEC-AFTER-1",
            employee_id=emp_dec_after.employee_id,
            pool_id=pool.pool_id,
            trustee_id=trustee.trustee_id,
            grant_date=date(2018, 1, 15),
            grant_type=models.GrantType.IL_102_CAPITAL_GAINS,
            total_options=8000,
            exercise_price=1.5,
            currency="ILS",
            trustee_deposit_date=date(2018, 2, 1)
        )

        g5 = models.Grant(
            grant_id="G-UNEXERCISED-1",
            employee_id=emp_unexercised.employee_id,
            pool_id=pool.pool_id,
            trustee_id=trustee.trustee_id,
            grant_date=date(2019, 2, 15),
            grant_type=models.GrantType.IL_102_CAPITAL_GAINS,
            total_options=4000,
            exercise_price=3.0,
            currency="ILS",
            trustee_deposit_date=date(2019, 3, 1)
        )

        db.add_all([g1, g2, g3, g4, g5])
        db.commit()

        # -------------------------------------------------------------
        # 5. יצירת לוח הבשלה (VestingSchedule)
        # -------------------------------------------------------------
        v1 = models.VestingSchedule(
            schedule_id="VS-001",
            grant_id=g1.grant_id,
            start_date=date(2021, 1, 15),
            cliff_months=12,
            total_months=48,
            paused_days_total=0
        )
        db.add(v1)
        db.commit()

        # ===============================================================
        # מכאן ואילך: תרחישי קצה נוספים לצורכי בדיקות (QA), לפי בקשה מפורשת.
        # בכוונה אין כאן שום ולידציה עסקית חדשה בקוד - רק דאטה. חלק
        # מהתרחישים האלה חושפים באגים אמיתיים וקיימים במערכת (למשל: אין
        # בדיקת גיל מינימלי ב-/admin/grants, אין סנכרון בין pool ל-grants
        # שבאמת הונפקו ב-QuickTurn, ו-termination_date לא מתאפס בחזרה
        # לעבודה ב-Boomerang). זה מכוון - ראו שיחת ה-seed המקורית.
        # ===============================================================

        # -------------------------------------------------------------
        # 6. חברות נוספות: 5 חברות ריקות (בלי עובדים) + חברה סגורה +
        # חברות ייעודיות לפיזור התרחישים הבאים על פני כמה חברות/פולים.
        # -------------------------------------------------------------
        print("🏢 יוצר חברות נוספות...")
        empty_companies = [
            ("COMP-EMPTY-1", "Nova Robotics Ltd", "IL"),
            ("COMP-EMPTY-2", "Quantum Foods Ltd", "IL"),
            ("COMP-EMPTY-3", "BrightWave Media Ltd", "IL"),
            ("COMP-EMPTY-4", "Orbit Logistics Inc", "US"),
            ("COMP-EMPTY-5", "Cedar Analytics Ltd", "IL"),
        ]
        for comp_id, comp_name, country in empty_companies:
            db.add(models.Company(company_id=comp_id, name=comp_name, country_code=country, is_active=True))

        db.add(models.Company(
            company_id="COMP-CLOSED-1",
            name="Legacy Startup (Dissolved) Ltd",
            country_code="IL",
            is_active=False,
        ))

        comp_us = models.Company(company_id="COMP-002", name="Meridian US Inc", country_code="US", is_active=True)
        comp_il_retire = models.Company(company_id="COMP-003", name="Har HaKerem Industries Ltd", country_code="IL", is_active=True)
        comp_quickturn = models.Company(company_id="COMP-004", name="QuickTurn Staffing Ltd", country_code="IL", is_active=True)
        comp_boomerang = models.Company(company_id="COMP-005", name="Boomerang Tech Ltd", country_code="IL", is_active=True)
        comp_patient = models.Company(company_id="COMP-006", name="Patient Capital Ltd", country_code="IL", is_active=True)
        db.add_all([comp_us, comp_il_retire, comp_quickturn, comp_boomerang, comp_patient])
        db.commit()

        pool_us = models.OptionPool(pool_id="POOL-US", company_id=comp_us.company_id,
                                     total_shares=200000, allocated_shares=0, unallocated_shares=200000)
        pool_il_retire = models.OptionPool(pool_id="POOL-IL-RETIRE", company_id=comp_il_retire.company_id,
                                            total_shares=100000, allocated_shares=0, unallocated_shares=100000)
        pool_quickturn = models.OptionPool(pool_id="POOL-QUICKTURN", company_id=comp_quickturn.company_id,
                                            total_shares=50000, allocated_shares=0, unallocated_shares=50000)
        pool_boomerang = models.OptionPool(pool_id="POOL-BOOMERANG", company_id=comp_boomerang.company_id,
                                            total_shares=200000, allocated_shares=0, unallocated_shares=200000)
        pool_patient = models.OptionPool(pool_id="POOL-PATIENT", company_id=comp_patient.company_id,
                                          total_shares=100000, allocated_shares=0, unallocated_shares=100000)
        db.add_all([pool_us, pool_il_retire, pool_quickturn, pool_boomerang, pool_patient])

        trustee_il_retire = models.Trustee(trustee_id="TRUSTEE-003", company_id=comp_il_retire.company_id,
                                            name="HaKerem Trust Services", registration_number="515222333")
        trustee_quickturn = models.Trustee(trustee_id="TRUSTEE-004", company_id=comp_quickturn.company_id,
                                            name="QuickTurn Trust Ltd", registration_number="515333444")
        trustee_boomerang = models.Trustee(trustee_id="TRUSTEE-005", company_id=comp_boomerang.company_id,
                                            name="Boomerang Trust Ltd", registration_number="515444555")
        trustee_patient = models.Trustee(trustee_id="TRUSTEE-006", company_id=comp_patient.company_id,
                                          name="Patient Capital Trust Ltd", registration_number="515555666")
        db.add_all([trustee_il_retire, trustee_quickturn, trustee_boomerang, trustee_patient])
        db.commit()

        # -------------------------------------------------------------
        # 7. 8 עובדים ללא חברה (company_id=NULL) - החברה שהעסיקה אותם נסגרה.
        # יתומים במכוון - אין קישור בפועל ל-COMP-CLOSED-1.
        # -------------------------------------------------------------
        print("👻 יוצר 8 עובדים ללא חברה...")
        for i in range(1, 9):
            db.add(models.Employee(
                employee_id=f"EMP-NOCOMPANY-{i}",
                company_id=None,
                first_name="עובד",
                last_name=f"ללא-חברה-{i}",
                email=f"nocompany{i}@orphaned.example",
                country_code="IL",
                status=models.EmployeeStatus.TERMINATED,
                hire_date=date(2018, 1, 1),
                termination_date=date(2024, 3, 1),
                birth_date=shift_months(today, -35 * 12),
            ))
        db.commit()

        # -------------------------------------------------------------
        # 8. 12 עובדים קטינים (מתחת לגיל 18) עם מענקים - חושף שאין שום
        # בדיקת גיל מינימלי ביצירת grant דרך /admin/grants.
        # -------------------------------------------------------------
        print("🧒 יוצר 12 עובדים קטינים עם מענקים...")
        for i in range(1, 13):
            age_years = 14 + (i % 4)  # 14..17
            birth = shift_months(today, -(age_years * 12 + i))
            hire = shift_months(today, -6)
            grant_date = shift_months(today, -5)

            emp = models.Employee(
                employee_id=f"EMP-MINOR-{i}",
                company_id=company.company_id,
                first_name="קטין",
                last_name=f"מתמחה-{i}",
                email=f"minor{i}@company.com",
                country_code="IL",
                status=models.EmployeeStatus.ACTIVE,
                hire_date=hire,
                birth_date=birth,
            )
            db.add(emp)
            db.flush()

            grant = models.Grant(
                grant_id=f"G-MINOR-{i}",
                employee_id=emp.employee_id,
                pool_id=pool.pool_id,
                trustee_id=trustee.trustee_id,
                grant_date=grant_date,
                grant_type=models.GrantType.IL_102_CAPITAL_GAINS,
                total_options=500,
                exercise_price=1.0,
                currency="ILS",
                trustee_deposit_date=shift_months(grant_date, 1),
            )
            db.add(grant)
            pool.allocated_shares += 500
            pool.unallocated_shares -= 500
            db.flush()

            db.add(models.VestingSchedule(
                schedule_id=f"VS-MINOR-{i}",
                grant_id=grant.grant_id,
                start_date=grant_date,
                cliff_months=12,
                total_months=48,
            ))
        db.commit()

        # -------------------------------------------------------------
        # 9. 15 עובדים בגיל פרישה - 8 בישראל (67, POOL-IL-RETIRE) + 7 בארה"ב
        # (66, POOL-US, בלי נאמן).
        # -------------------------------------------------------------
        print("👴 יוצר 15 עובדים בגיל פרישה (IL+US)...")
        for i in range(1, 9):
            birth = shift_months(today, -(67 * 12 + i * 3))
            hire = shift_months(birth, 25 * 12)
            grant_date = shift_months(hire, 12)

            emp = models.Employee(
                employee_id=f"EMP-RETIRE-IL-{i}",
                company_id=comp_il_retire.company_id,
                first_name="ותיק",
                last_name=f"IL-{i}",
                email=f"retireil{i}@harkerem.example",
                country_code="IL",
                status=models.EmployeeStatus.ACTIVE,
                hire_date=hire,
                birth_date=birth,
            )
            db.add(emp)
            db.flush()

            grant = models.Grant(
                grant_id=f"G-RETIRE-IL-{i}",
                employee_id=emp.employee_id,
                pool_id=pool_il_retire.pool_id,
                trustee_id=trustee_il_retire.trustee_id,
                grant_date=grant_date,
                grant_type=models.GrantType.IL_102_CAPITAL_GAINS,
                total_options=2000,
                exercise_price=4.0,
                currency="ILS",
                trustee_deposit_date=shift_months(grant_date, 1),
            )
            db.add(grant)
            pool_il_retire.allocated_shares += 2000
            pool_il_retire.unallocated_shares -= 2000
            db.flush()

            db.add(models.VestingSchedule(
                schedule_id=f"VS-RETIRE-IL-{i}",
                grant_id=grant.grant_id,
                start_date=grant_date,
                cliff_months=12,
                total_months=48,
            ))
        db.commit()

        for i in range(1, 8):
            birth = shift_months(today, -(66 * 12 + i * 3))
            hire = shift_months(birth, 25 * 12)
            grant_date = shift_months(hire, 12)
            grant_type = models.GrantType.US_ISO if i % 2 == 0 else models.GrantType.US_NSO

            emp = models.Employee(
                employee_id=f"EMP-RETIRE-US-{i}",
                company_id=comp_us.company_id,
                first_name="Senior",
                last_name=f"Employee-{i}",
                email=f"retireus{i}@meridian.example",
                country_code="US",
                status=models.EmployeeStatus.ACTIVE,
                hire_date=hire,
                birth_date=birth,
            )
            db.add(emp)
            db.flush()

            grant = models.Grant(
                grant_id=f"G-RETIRE-US-{i}",
                employee_id=emp.employee_id,
                pool_id=pool_us.pool_id,
                trustee_id=None,
                grant_date=grant_date,
                grant_type=grant_type,
                total_options=2000,
                exercise_price=4.0,
                currency="USD",
                trustee_deposit_date=None,
            )
            db.add(grant)
            pool_us.allocated_shares += 2000
            pool_us.unallocated_shares -= 2000
            db.flush()

            db.add(models.VestingSchedule(
                schedule_id=f"VS-RETIRE-US-{i}",
                grant_id=grant.grant_id,
                start_date=grant_date,
                cliff_months=12,
                total_months=48,
            ))
        db.commit()

        # -------------------------------------------------------------
        # 10. 5 עובדים שנפטרו לפני ה-cliff (0 אופציות הבשילו) - COMP-001
        # -------------------------------------------------------------
        print("🕯️ יוצר 5 עובדים שנפטרו לפני ה-cliff...")
        death_after_grant_preclif = [7, 8, 9, 10, 11]  # תמיד לפני cliff של 12 חודש
        for i in range(1, 6):
            grant_date = shift_months(today, -30 - i)
            death_date = shift_months(grant_date, death_after_grant_preclif[i - 1])

            emp = models.Employee(
                employee_id=f"EMP-DEC-PRECLIFF-{i}",
                company_id=company.company_id,
                first_name="נפטר",
                last_name=f"לפני-cliff-{i}",
                email=f"decpre{i}@company.com",
                country_code="IL",
                status=models.EmployeeStatus.DECEASED,
                hire_date=shift_months(grant_date, -3),
                termination_date=death_date,
                birth_date=shift_months(today, -40 * 12),
            )
            db.add(emp)
            db.flush()

            grant = models.Grant(
                grant_id=f"G-DEC-PRECLIFF-{i}",
                employee_id=emp.employee_id,
                pool_id=pool.pool_id,
                trustee_id=trustee.trustee_id,
                grant_date=grant_date,
                grant_type=models.GrantType.IL_102_CAPITAL_GAINS,
                total_options=3000,
                exercise_price=2.0,
                currency="ILS",
                trustee_deposit_date=shift_months(grant_date, 1),
            )
            db.add(grant)
            pool.allocated_shares += 3000
            pool.unallocated_shares -= 3000
            db.flush()

            db.add(models.VestingSchedule(
                schedule_id=f"VS-DEC-PRECLIFF-{i}",
                grant_id=grant.grant_id,
                start_date=grant_date,
                cliff_months=12,
                total_months=48,
            ))
        db.commit()

        # -------------------------------------------------------------
        # 11. 5 עובדים שנפטרו אחרי ה-cliff אך האופציות עדיין בנאמנות (סעיף
        # 102, חסימת שנתיים עדיין בתוקף היום) - COMP-001
        # -------------------------------------------------------------
        print("🕯️ יוצר 5 עובדים שנפטרו אחרי ה-cliff אך עדיין בנאמנות...")
        # grant_ago > death_after (מוות בעבר) > 12 (אחרי ה-cliff), ו-deposit_ago < 24
        # (חלון החסימה של הנאמנות עדיין לא נגמר היום).
        grant_ago_intrust = [20, 21, 22, 23, 24]
        death_after_intrust = [13, 14, 15, 16, 17]
        for i in range(1, 6):
            grant_date = shift_months(today, -grant_ago_intrust[i - 1])
            deposit_date = shift_months(grant_date, 1)
            death_date = shift_months(grant_date, death_after_intrust[i - 1])

            emp = models.Employee(
                employee_id=f"EMP-DEC-INTRUSTEE-{i}",
                company_id=company.company_id,
                first_name="נפטר",
                last_name=f"בנאמנות-{i}",
                email=f"decintrust{i}@company.com",
                country_code="IL",
                status=models.EmployeeStatus.DECEASED,
                hire_date=shift_months(grant_date, -6),
                termination_date=death_date,
                birth_date=shift_months(today, -45 * 12),
            )
            db.add(emp)
            db.flush()

            grant = models.Grant(
                grant_id=f"G-DEC-INTRUSTEE-{i}",
                employee_id=emp.employee_id,
                pool_id=pool.pool_id,
                trustee_id=trustee.trustee_id,
                grant_date=grant_date,
                grant_type=models.GrantType.IL_102_CAPITAL_GAINS,
                total_options=4000,
                exercise_price=2.5,
                currency="ILS",
                trustee_deposit_date=deposit_date,
            )
            db.add(grant)
            pool.allocated_shares += 4000
            pool.unallocated_shares -= 4000
            db.flush()

            db.add(models.VestingSchedule(
                schedule_id=f"VS-DEC-INTRUSTEE-{i}",
                grant_id=grant.grant_id,
                start_date=grant_date,
                cliff_months=12,
                total_months=48,
            ))
        db.commit()

        # -------------------------------------------------------------
        # 12. 10 עובדים שקיבלו מענק ופוטרו חודש אחרי - COMP-004/POOL-QUICKTURN.
        # בכוונה לא מעדכן את pool.allocated/unallocated_shares - מדמה תהליך
        # עזיבה שלא רץ דרך ה-API (פער יישוב חשבונות אמיתי לבדיקה).
        # -------------------------------------------------------------
        print("⚡ יוצר 10 עובדים שפוטרו חודש אחרי המענק (QuickTurn)...")
        for i in range(1, 11):
            grant_date = shift_months(today, -6 - i)
            termination_date = shift_months(grant_date, 1)

            emp = models.Employee(
                employee_id=f"EMP-GRANT-TERM1M-{i}",
                company_id=comp_quickturn.company_id,
                first_name="עובד",
                last_name=f"פוטר-מהיר-{i}",
                email=f"quickterm{i}@quickturn.example",
                country_code="IL",
                status=models.EmployeeStatus.TERMINATED,
                hire_date=shift_months(grant_date, -2),
                termination_date=termination_date,
                birth_date=shift_months(today, -30 * 12),
            )
            db.add(emp)
            db.flush()

            grant = models.Grant(
                grant_id=f"G-GRANT-TERM1M-{i}",
                employee_id=emp.employee_id,
                pool_id=pool_quickturn.pool_id,
                trustee_id=trustee_quickturn.trustee_id,
                grant_date=grant_date,
                grant_type=models.GrantType.IL_102_CAPITAL_GAINS,
                total_options=1500,
                exercise_price=3.0,
                currency="ILS",
                trustee_deposit_date=shift_months(grant_date, 1),
            )
            db.add(grant)
            # בכוונה: אין עדכון ל-pool_quickturn.allocated/unallocated_shares כאן.
            db.flush()

            db.add(models.VestingSchedule(
                schedule_id=f"VS-GRANT-TERM1M-{i}",
                grant_id=grant.grant_id,
                start_date=grant_date,
                cliff_months=12,
                total_months=48,
            ))
        db.commit()

        # -------------------------------------------------------------
        # 13. 7 עובדים שעזבו וחזרו עם אותם תנאי מענק - COMP-005/POOL-BOOMERANG.
        # ההבשלה ממשיכה מיום החזרה דרך paused_days_total. שימו לב:
        # termination_date נשאר עם התאריך ההיסטורי גם שהעובד כרגע ACTIVE.
        # -------------------------------------------------------------
        print("🔁 יוצר 7 עובדים שעזבו וחזרו לעבודה (Boomerang)...")
        gap_days_options = [30, 60, 90, 180, 365, 400, 500]
        for i, gap_days in enumerate(gap_days_options, start=1):
            grant_date = shift_months(today, -60 - i)
            left_date = shift_months(grant_date, 20 + i)  # אחרי ה-cliff

            emp = models.Employee(
                employee_id=f"EMP-REHIRE-{i}",
                company_id=comp_boomerang.company_id,
                first_name="חוזר",
                last_name=f"לעבודה-{i}",
                email=f"rehire{i}@boomerang.example",
                country_code="IL",
                status=models.EmployeeStatus.ACTIVE,
                hire_date=grant_date,
                termination_date=left_date,
                birth_date=shift_months(today, -35 * 12),
            )
            db.add(emp)
            db.flush()

            grant = models.Grant(
                grant_id=f"G-REHIRE-{i}",
                employee_id=emp.employee_id,
                pool_id=pool_boomerang.pool_id,
                trustee_id=trustee_boomerang.trustee_id,
                grant_date=grant_date,
                grant_type=models.GrantType.IL_102_CAPITAL_GAINS,
                total_options=6000,
                exercise_price=3.5,
                currency="ILS",
                trustee_deposit_date=shift_months(grant_date, 1),
            )
            db.add(grant)
            pool_boomerang.allocated_shares += 6000
            pool_boomerang.unallocated_shares -= 6000
            db.flush()

            db.add(models.VestingSchedule(
                schedule_id=f"VS-REHIRE-{i}",
                grant_id=grant.grant_id,
                start_date=grant_date,
                cliff_months=12,
                total_months=48,
                paused_days_total=gap_days,
            ))
        db.commit()

        # -------------------------------------------------------------
        # 14. 7 עובדים עם הבשלה מלאה שלא מומשה - COMP-006/POOL-PATIENT.
        # פרקי זמן שונים מאז השלמת ההבשלה: שבוע, חודש, חודשיים, 3 חודשים,
        # שנה, שנתיים, 3 שנים.
        # -------------------------------------------------------------
        print("⏳ יוצר 7 עובדים עם הבשלה מלאה שלא מומשה (Patient Capital)...")
        elapsed_options = [
            ("1WEEK", 7, None),
            ("1MONTH", None, 1),
            ("2MONTHS", None, 2),
            ("3MONTHS", None, 3),
            ("1YEAR", None, 12),
            ("2YEARS", None, 24),
            ("3YEARS", None, 36),
        ]
        for label, days, months in elapsed_options:
            fully_vested_date = today - timedelta(days=days) if days is not None else shift_months(today, -months)
            total_months = 48
            start_date = shift_months(fully_vested_date, -total_months)

            emp = models.Employee(
                employee_id=f"EMP-UNEXERCISED-{label}",
                company_id=comp_patient.company_id,
                first_name="לא-מימש",
                last_name=label,
                email=f"unexercised_{label.lower()}@patientcap.example",
                country_code="IL",
                status=models.EmployeeStatus.ACTIVE,
                hire_date=start_date,
                birth_date=shift_months(today, -33 * 12),
            )
            db.add(emp)
            db.flush()

            grant = models.Grant(
                grant_id=f"G-UNEXERCISED-{label}",
                employee_id=emp.employee_id,
                pool_id=pool_patient.pool_id,
                trustee_id=trustee_patient.trustee_id,
                grant_date=start_date,
                grant_type=models.GrantType.IL_102_CAPITAL_GAINS,
                total_options=2500,
                exercise_price=2.0,
                currency="ILS",
                trustee_deposit_date=shift_months(start_date, 1),
            )
            db.add(grant)
            pool_patient.allocated_shares += 2500
            pool_patient.unallocated_shares -= 2500
            db.flush()

            db.add(models.VestingSchedule(
                schedule_id=f"VS-UNEXERCISED-{label}",
                grant_id=grant.grant_id,
                start_date=start_date,
                cliff_months=12,
                total_months=total_months,
            ))
        db.commit()

        # -------------------------------------------------------------
        # 15. הרחבת תיק העבודה: 4 מתוך 5 הנאמנים הקיימים (TRUSTEE-001,
        # TRUSTEE-003, TRUSTEE-004, TRUSTEE-005) מקבלים כל אחד עוד 3 חברות
        # חדשות, כל חברה עם 15 עובדים "רגילים" (לא edge-case) - דאטה בנפח
        # לבדיקת תצוגת "תיק עבודה" לנאמן על פני כמה חברות בבת אחת.
        # TRUSTEE-006 (Patient Capital) נשאר בלי הרחבה בכוונה.
        # -------------------------------------------------------------
        print("🏢 יוצר 12 חברות חדשות + 180 עובדים תחת 4 נאמנים קיימים...")
        trustee_expansion = [
            (trustee.trustee_id, [
                ("COMP-007", "Sapphire Cyber"),
                ("COMP-008", "GreenField AgriTech"),
                ("COMP-009", "Northbridge Fintech"),
            ]),
            (trustee_il_retire.trustee_id, [
                ("COMP-010", "Skyline Biomed"),
                ("COMP-011", "Coral Reef Gaming"),
                ("COMP-012", "Ironclad Defense Systems"),
            ]),
            (trustee_quickturn.trustee_id, [
                ("COMP-013", "Velvet Retail Group"),
                ("COMP-014", "Lighthouse Logistics"),
                ("COMP-015", "Crescent Health"),
            ]),
            (trustee_boomerang.trustee_id, [
                ("COMP-016", "Falcon Aerospace"),
                ("COMP-017", "Everest Construction"),
                ("COMP-018", "Pinewood Media"),
            ]),
        ]

        emp_counter = 0
        for trustee_id_for_batch, companies_for_trustee in trustee_expansion:
            for comp_id, comp_name in companies_for_trustee:
                new_company = models.Company(company_id=comp_id, name=f"{comp_name} Ltd", country_code="IL", is_active=True)
                db.add(new_company)
                db.commit()

                new_pool = models.OptionPool(pool_id=f"POOL-{comp_id}", company_id=new_company.company_id,
                                              total_shares=300000, allocated_shares=0, unallocated_shares=300000)
                db.add(new_pool)
                db.commit()

                for j in range(1, 16):
                    emp_counter += 1
                    hire = shift_months(today, -(6 + (emp_counter % 48)))
                    grant_date = hire
                    total_opts = 1000 + (emp_counter % 10) * 500

                    emp = models.Employee(
                        employee_id=f"EMP-{comp_id}-{j}",
                        company_id=new_company.company_id,
                        first_name="עובד",
                        last_name=f"{comp_name.split()[0]}-{j}",
                        email=f"emp{j}@{comp_id.lower()}.example",
                        country_code="IL",
                        status=models.EmployeeStatus.ACTIVE,
                        hire_date=hire,
                        birth_date=shift_months(today, -(25 + (emp_counter % 20)) * 12),
                    )
                    db.add(emp)
                    db.flush()

                    grant = models.Grant(
                        grant_id=f"G-{comp_id}-{j}",
                        employee_id=emp.employee_id,
                        pool_id=new_pool.pool_id,
                        trustee_id=trustee_id_for_batch,
                        grant_date=grant_date,
                        grant_type=models.GrantType.IL_102_CAPITAL_GAINS,
                        total_options=total_opts,
                        exercise_price=2.0 + (emp_counter % 5),
                        currency="ILS",
                        trustee_deposit_date=shift_months(grant_date, 1),
                    )
                    db.add(grant)
                    new_pool.allocated_shares += total_opts
                    new_pool.unallocated_shares -= total_opts
                    db.flush()

                    db.add(models.VestingSchedule(
                        schedule_id=f"VS-{comp_id}-{j}",
                        grant_id=grant.grant_id,
                        start_date=grant_date,
                        cliff_months=12,
                        total_months=48,
                    ))
                db.commit()

        # -------------------------------------------------------------
        # 16. "מעבדת באגים" (COMP-BUGS) - חברה/פול/נאמן ייעודיים, עם 4 עובדים
        # שכל אחד מהם בנוי ידנית לחשוף באג ספציפי אחד, מבודד מבאגים אחרים:
        #   - EMP-BUG-OVERVEST-1: בקשת מימוש (PENDING) על יותר אופציות משבאמת
        #     הבשילו - בודק שאישור admin/trustee לא בודק vested_options בפועל.
        #   - EMP-BUG-DUPLICATE-1: שתי בקשות PENDING על אותו grant שסכומן עולה
        #     על סה"כ האופציות - בודק שאין הגנה מפני אישור כפול/חופף.
        #   - EMP-BUG-EARLYHOLD-1: מענק מלא בהבשלה אבל trustee_deposit_date
        #     מלפני 3 חודשים בלבד (חסימת 2 שנים עדיין לא נגמרה) עם בקשת מימוש
        #     PENDING - בודק שאישור לא בודק is_trustee_holding_period_met.
        #   - EMP-BUG-FEB29-1: לוח הבשלה עם start_date=29/2/2024 ו-cliff=24
        #     חודשים -> מנסה לבנות date(2026,2,29) שלא קיים -> קריסה אמיתית
        #     ב-DeterministicESOPEngine.calculate_vested_options. בכוונה בלי
        #     trustee_id כדי שהקריסה לא תחסום את תיק העבודה של נאמן אמיתי.
        # -------------------------------------------------------------
        print("🧪 יוצר מעבדת באגים (COMP-BUGS) עם 4 תרחישי באג מבודדים...")
        comp_bugs = models.Company(company_id="COMP-BUGS", name="Bug Reproduction Lab Ltd", country_code="IL", is_active=True)
        db.add(comp_bugs)
        db.commit()

        pool_bugs = models.OptionPool(pool_id="POOL-BUGS", company_id=comp_bugs.company_id,
                                       total_shares=100000, allocated_shares=0, unallocated_shares=100000)
        db.add(pool_bugs)
        trustee_bugs = models.Trustee(trustee_id="TRUSTEE-BUGS", company_id=comp_bugs.company_id,
                                       name="Bug Lab Trust Services", registration_number="515999000")
        db.add(trustee_bugs)
        db.commit()

        # --- EMP-BUG-OVERVEST-1: מבשיל חלקית (~1300 מתוך 4800), בקשה על 4000 ---
        overvest_grant_date = shift_months(today, -13)
        emp_overvest = models.Employee(
            employee_id="EMP-BUG-OVERVEST-1", company_id=comp_bugs.company_id,
            first_name="באג", last_name="מימוש-יתר", email="bug.overvest@buglab.example",
            country_code="IL", status=models.EmployeeStatus.ACTIVE,
            hire_date=shift_months(overvest_grant_date, -3), birth_date=shift_months(today, -30 * 12),
        )
        db.add(emp_overvest)
        db.flush()
        grant_overvest = models.Grant(
            grant_id="G-BUG-OVERVEST-1", employee_id=emp_overvest.employee_id, pool_id=pool_bugs.pool_id,
            trustee_id=trustee_bugs.trustee_id, grant_date=overvest_grant_date,
            grant_type=models.GrantType.IL_102_CAPITAL_GAINS, total_options=4800, exercise_price=2.0,
            currency="ILS", trustee_deposit_date=shift_months(overvest_grant_date, 1),
        )
        db.add(grant_overvest)
        pool_bugs.allocated_shares += 4800
        pool_bugs.unallocated_shares -= 4800
        db.flush()
        db.add(models.VestingSchedule(schedule_id="VS-BUG-OVERVEST-1", grant_id=grant_overvest.grant_id,
                                       start_date=overvest_grant_date, cliff_months=12, total_months=48))
        db.add(models.ExerciseRequest(
            request_id="REQ-BUG-OVERVEST-1", grant_id=grant_overvest.grant_id, employee_id=emp_overvest.employee_id,
            options_requested=4000, status=models.ExerciseRequestStatus.PENDING,
        ))

        # --- EMP-BUG-DUPLICATE-1: מבשיל מלא (3000/3000), 2 בקשות PENDING של 2000 כ"א ---
        duplicate_grant_date = shift_months(today, -60)
        emp_duplicate = models.Employee(
            employee_id="EMP-BUG-DUPLICATE-1", company_id=comp_bugs.company_id,
            first_name="באג", last_name="בקשה-כפולה", email="bug.duplicate@buglab.example",
            country_code="IL", status=models.EmployeeStatus.ACTIVE,
            hire_date=shift_months(duplicate_grant_date, -3), birth_date=shift_months(today, -35 * 12),
        )
        db.add(emp_duplicate)
        db.flush()
        grant_duplicate = models.Grant(
            grant_id="G-BUG-DUPLICATE-1", employee_id=emp_duplicate.employee_id, pool_id=pool_bugs.pool_id,
            trustee_id=trustee_bugs.trustee_id, grant_date=duplicate_grant_date,
            grant_type=models.GrantType.IL_102_CAPITAL_GAINS, total_options=3000, exercise_price=2.0,
            currency="ILS", trustee_deposit_date=shift_months(duplicate_grant_date, 1),
        )
        db.add(grant_duplicate)
        pool_bugs.allocated_shares += 3000
        pool_bugs.unallocated_shares -= 3000
        db.flush()
        db.add(models.VestingSchedule(schedule_id="VS-BUG-DUPLICATE-1", grant_id=grant_duplicate.grant_id,
                                       start_date=duplicate_grant_date, cliff_months=12, total_months=48))
        db.add(models.ExerciseRequest(
            request_id="REQ-BUG-DUPLICATE-1A", grant_id=grant_duplicate.grant_id, employee_id=emp_duplicate.employee_id,
            options_requested=2000, status=models.ExerciseRequestStatus.PENDING,
        ))
        db.add(models.ExerciseRequest(
            request_id="REQ-BUG-DUPLICATE-1B", grant_id=grant_duplicate.grant_id, employee_id=emp_duplicate.employee_id,
            options_requested=2000, status=models.ExerciseRequestStatus.PENDING,
        ))

        # --- EMP-BUG-EARLYHOLD-1: מבשיל מלא, הפקדה לפני 3 חודשים בלבד (חסימה עדיין פעילה) ---
        earlyhold_grant_date = shift_months(today, -60)
        emp_earlyhold = models.Employee(
            employee_id="EMP-BUG-EARLYHOLD-1", company_id=comp_bugs.company_id,
            first_name="באג", last_name="חסימה-מוקדמת", email="bug.earlyhold@buglab.example",
            country_code="IL", status=models.EmployeeStatus.ACTIVE,
            hire_date=shift_months(earlyhold_grant_date, -3), birth_date=shift_months(today, -32 * 12),
        )
        db.add(emp_earlyhold)
        db.flush()
        grant_earlyhold = models.Grant(
            grant_id="G-BUG-EARLYHOLD-1", employee_id=emp_earlyhold.employee_id, pool_id=pool_bugs.pool_id,
            trustee_id=trustee_bugs.trustee_id, grant_date=earlyhold_grant_date,
            grant_type=models.GrantType.IL_102_CAPITAL_GAINS, total_options=2000, exercise_price=2.0,
            currency="ILS", trustee_deposit_date=shift_months(today, -3),
        )
        db.add(grant_earlyhold)
        pool_bugs.allocated_shares += 2000
        pool_bugs.unallocated_shares -= 2000
        db.flush()
        db.add(models.VestingSchedule(schedule_id="VS-BUG-EARLYHOLD-1", grant_id=grant_earlyhold.grant_id,
                                       start_date=earlyhold_grant_date, cliff_months=12, total_months=48))
        db.add(models.ExerciseRequest(
            request_id="REQ-BUG-EARLYHOLD-1", grant_id=grant_earlyhold.grant_id, employee_id=emp_earlyhold.employee_id,
            options_requested=2000, status=models.ExerciseRequestStatus.PENDING,
        ))

        # --- EMP-BUG-FEB29-1: start_date=29/2/2024 + cliff=24 חודשים -> date(2026,2,29) לא קיים ---
        feb29_grant_date = date(2024, 2, 29)
        emp_feb29 = models.Employee(
            employee_id="EMP-BUG-FEB29-1", company_id=comp_bugs.company_id,
            first_name="באג", last_name="29-בפברואר", email="bug.feb29@buglab.example",
            country_code="IL", status=models.EmployeeStatus.ACTIVE,
            hire_date=date(2024, 1, 1), birth_date=shift_months(today, -28 * 12),
        )
        db.add(emp_feb29)
        db.flush()
        grant_feb29 = models.Grant(
            grant_id="G-BUG-FEB29-1", employee_id=emp_feb29.employee_id, pool_id=pool_bugs.pool_id,
            trustee_id=None, grant_date=feb29_grant_date,
            grant_type=models.GrantType.IL_102_CAPITAL_GAINS, total_options=1000, exercise_price=1.0,
            currency="ILS", trustee_deposit_date=date(2024, 3, 1),
        )
        db.add(grant_feb29)
        pool_bugs.allocated_shares += 1000
        pool_bugs.unallocated_shares -= 1000
        db.flush()
        db.add(models.VestingSchedule(schedule_id="VS-BUG-FEB29-1", grant_id=grant_feb29.grant_id,
                                       start_date=feb29_grant_date, cliff_months=24, total_months=48))
        db.commit()

        # -------------------------------------------------------------
        # 17. טבלאות מס versioned לפי תאריך - *** נתוני דמו לתרגול QA בלבד,
        # לא חוק מס אמיתי (ראו official_source_url="DEMO-NOT-REAL-TAX-LAW") ***
        # שיעור שטוח (TaxRatesHistory) לכל הסוגים מלבד IL_102_WORK_INCOME, ו-2
        # גרסאות לכל שילוב (תאריכי תחולה שונים) כדי שבחירת הגרסה הנכונה לפי
        # תאריך המימוש תהיה ניתנת לבדיקה בפועל. + מדרגות פרוגרסיביות אמיתיות
        # (IncomeTaxBracket) ל-IL_102_WORK_INCOME, גם הן ב-2 גרסאות.
        # -------------------------------------------------------------
        print("🧾 יוצר טבלאות מס versioned (דמו בלבד)...")
        DEMO_SOURCE = "DEMO-NOT-REAL-TAX-LAW"

        flat_tax_rows = [
            ("IL_102_CAPITAL_GAINS", "IL", date(2020, 1, 1), 0.25),
            ("IL_102_CAPITAL_GAINS", "IL", date(2025, 1, 1), 0.28),
            ("US_ISO", "US", date(2020, 1, 1), 0.20),
            ("US_ISO", "US", date(2025, 1, 1), 0.22),
            ("US_NSO", "US", date(2020, 1, 1), 0.30),
            ("US_NSO", "US", date(2025, 1, 1), 0.32),
        ]
        for grant_type, country, eff_date, rate in flat_tax_rows:
            db.add(models.TaxRatesHistory(
                tax_rule_id=f"TAX-{grant_type}-{country}-{eff_date.isoformat()}",
                country_code=country, grant_type=grant_type, effective_start_date=eff_date,
                capital_gains_rate=rate, official_source_url=DEMO_SOURCE,
            ))

        bracket_versions = [
            (date(2020, 1, 1), [
                (0, 0, 75000, 0.10), (1, 75000, 150000, 0.20), (2, 150000, 300000, 0.30), (3, 300000, None, 0.40),
            ]),
            (date(2025, 1, 1), [
                (0, 0, 80000, 0.10), (1, 80000, 160000, 0.22), (2, 160000, 320000, 0.32), (3, 320000, None, 0.42),
            ]),
        ]
        for eff_date, brackets in bracket_versions:
            for order, min_amt, max_amt, rate in brackets:
                db.add(models.IncomeTaxBracket(
                    bracket_id=f"BRACKET-IL_102_WORK_INCOME-IL-{eff_date.isoformat()}-{order}",
                    country_code="IL", grant_type="IL_102_WORK_INCOME", effective_start_date=eff_date,
                    bracket_order=order, min_amount=min_amt, max_amount=max_amt, rate=rate,
                    official_source_url=DEMO_SOURCE,
                ))
        db.commit()

        # מחיר מניה עדכני ל-COMP-001, גבוה משמעותית ממחירי המימוש הקיימים - בלי
        # זה כל "הרווח" בסימולציות תמיד יוצא 0 (fallback ל-exercise_price עצמו).
        db.add(models.StockPricesHistory(
            price_id="PRICE-COMP-001-1", company_id="COMP-001", price_date=today, fmv_price=50.0, currency="ILS",
        ))

        # עובד ייעודי ל-IL_102_WORK_INCOME, מבשיל מלא, כדי שאפשר לבדוק את מדרגות
        # המס בפועל על גרסאות שונות (exercise_date לפני/אחרי 2025-01-01).
        work_income_grant_date = shift_months(today, -60)
        emp_work_income = models.Employee(
            employee_id="EMP-TAX-WORKINCOME-1", company_id="COMP-001",
            first_name="מסלול", last_name="הכנסת-עבודה", email="tax.workincome@company.com",
            country_code="IL", status=models.EmployeeStatus.ACTIVE,
            hire_date=shift_months(work_income_grant_date, -3), birth_date=shift_months(today, -34 * 12),
        )
        db.add(emp_work_income)
        db.flush()
        grant_work_income = models.Grant(
            grant_id="G-TAX-WORKINCOME-1", employee_id=emp_work_income.employee_id, pool_id="POOL-2021",
            trustee_id="TRUSTEE-001", grant_date=work_income_grant_date,
            grant_type=models.GrantType.IL_102_WORK_INCOME, total_options=10000, exercise_price=1.0,
            currency="ILS", trustee_deposit_date=shift_months(work_income_grant_date, 1),
        )
        db.add(grant_work_income)
        pool.allocated_shares += 10000
        pool.unallocated_shares -= 10000
        db.flush()
        db.add(models.VestingSchedule(schedule_id="VS-TAX-WORKINCOME-1", grant_id=grant_work_income.grant_id,
                                       start_date=work_income_grant_date, cliff_months=12, total_months=48))
        db.commit()

        # -------------------------------------------------------------
        # 18. יצירת משתמשי התחברות (Users) לשלושת הפורטלים - כניסה אחת לכל
        # חברה (COMPANY_ADMIN), כניסה אחת לכל נאמן (TRUSTEE), וכניסה לקבוצת
        # עובדים מייצגת (EMPLOYEE) לצורך בדיקות. אותה סיסמת דמו לכולם.
        # -------------------------------------------------------------
        print("🔐 יוצר משתמשי התחברות (Users) לכל התפקידים...")
        DEMO_PASSWORD = "Demo1234!"

        for comp in db.query(models.Company).all():
            pw_hash, salt = hash_password(DEMO_PASSWORD)
            db.add(models.User(
                username=f"admin@{comp.company_id.lower()}.demo",
                password_hash=pw_hash, password_salt=salt,
                role=models.UserRole.COMPANY_ADMIN, company_id=comp.company_id,
            ))

        for tr in db.query(models.Trustee).all():
            pw_hash, salt = hash_password(DEMO_PASSWORD)
            db.add(models.User(
                username=f"trustee@{tr.trustee_id.lower()}.demo",
                password_hash=pw_hash, password_salt=salt,
                role=models.UserRole.TRUSTEE, trustee_id=tr.trustee_id,
            ))

        demo_employee_ids = [
            "EMP-001", "EMP-UNDERAGE-1", "EMP-MINOR-1", "EMP-MINOR-2",
            "EMP-RETIRE-IL-1", "EMP-RETIRE-US-1", "EMP-UNEXERCISED-1YEAR",
            "EMP-REHIRE-1", "EMP-COMP-007-1", "EMP-COMP-010-1", "EMP-COMP-013-1",
            # נציגים נוספים - כניסה ייעודית לכל תרחיש/באג בקטגוריה B, שלא היה
            # להם login עד כה (ראו qa_bug_accounts.md לרשימה המלאה עם הסברים):
            "EMP-RETIRED-1", "EMP-DEC-BEFORE-1", "EMP-DEC-AFTER-1", "EMP-UNEXERCISED-1",
            "EMP-GRANT-TERM1M-1", "EMP-DEC-PRECLIFF-1", "EMP-DEC-INTRUSTEE-1", "EMP-NOCOMPANY-1",
            # תרחישי באג ייעודיים (COMP-BUGS) - ראו סעיף 16 למעלה:
            "EMP-BUG-OVERVEST-1", "EMP-BUG-DUPLICATE-1", "EMP-BUG-EARLYHOLD-1", "EMP-BUG-FEB29-1",
            # מדרגות מס פרוגרסיביות (IL_102_WORK_INCOME) - ראו סעיף 17 למעלה:
            "EMP-TAX-WORKINCOME-1",
        ]
        for emp_id in demo_employee_ids:
            emp = db.query(models.Employee).filter(models.Employee.employee_id == emp_id).first()
            if emp:
                pw_hash, salt = hash_password(DEMO_PASSWORD)
                db.add(models.User(
                    username=emp.email,
                    password_hash=pw_hash, password_salt=salt,
                    role=models.UserRole.EMPLOYEE, employee_id=emp.employee_id,
                ))

        db.commit()

        print("✅ הזרקת הנתונים הושלמה בהצלחה ללא שום שגיאה!")

    except Exception as e:
        print(f"❌ שגיאה במהלך הזרקת הנתונים: {e}")
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()
