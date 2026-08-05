import calendar
from datetime import date, timedelta
from backend.app.models import Grant, VestingSchedule, Employee, EmployeeStatus


class MissingVestingScheduleError(ValueError):
    """למענק אין VestingSchedule, ולכן *אי אפשר לדעת* כמה הבשיל.

    הסיבה שזו חריגה ולא 0.0: "לא הבשיל כלום" ו"אין נתוני הבשלה" הם שני מצבים
    שונים לגמרי, ועובד שרואה 0 אינו יכול להבחין ביניהם. החזרת 0 הפכה נתון חסר
    לתשובה עסקית שגויה. מי שקורא חייב להחליט מה להציג במצב הזה.
    """


# האם "אותו יום בחודש" שאינו קיים בחודש היעד (29/2, או 31 בחודש בן 30) נסגר
# אחורה ליום האחרון של החודש או קדימה ליום הראשון של החודש הבא.
CLAMP_BACK = "clamp_back"
ROLL_FORWARD = "roll_forward"


def shift_months(anchor: date, months: int, on_missing_day: str = CLAMP_BACK) -> date:
    """הזזת תאריך במספר חודשים, בלי להתרסק על יום שאינו קיים בחודש היעד.

    הקוד הקודם בנה ``date(year + n, month, day)`` ישירות, ולכן הפקדה או תחילת
    הבשלה ב-29/2 הפילו את המנוע ב-ValueError בשנה שאינה מעוברת. כאן היום החסר
    נסגר לכיוון *מוצהר במפורש בכל קריאה*, כי הכיוון הוא החלטה עסקית ולא טכנית:
    סגירה אחורה מקדימה את המועד ביום, סגירה קדימה מאחרת אותו ביום, ובתקופת
    חסימה סטטוטורית יום אחד הוא הפרש מס אמיתי.
    """
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    last_day_of_month = calendar.monthrange(year, month)[1]

    if anchor.day <= last_day_of_month:
        return date(year, month, anchor.day)
    if on_missing_day == CLAMP_BACK:
        return date(year, month, last_day_of_month)
    return date(year, month, last_day_of_month) + timedelta(days=1)


class DeterministicESOPEngine:

    # חלון מורחב במקרה פטירה - נוהג מקובל בתוכניות אופציות (לרוב ~12 חודש), לא קבוע
    # בחוק כמו חסימת הנאמנות של 2 שנים; לכן קבוע כאן ולא ניתן להגדרה פר-מענק.
    POST_TERMINATION_WINDOW_DAYS_DEATH = 365

    # חסימת הנאמנות בסעיף 102 במסלול רווח הון - 24 חודשים מיום ההפקדה.
    TRUSTEE_HOLDING_MONTHS = 24

    @staticmethod
    def calculate_vested_options(grant: Grant, schedule: VestingSchedule, target_date: date) -> float:
        """חישוב קשיח בלתי תלוי ב-AI עבור הבשלת אופציות.

        זורק MissingVestingScheduleError כשאין לוח הבשלה - ראו שם.
        """
        if not schedule:
            raise MissingVestingScheduleError(
                f"Grant {getattr(grant, 'grant_id', '?')} has no vesting schedule; "
                "vested amount is unknown, not zero"
            )

        adjusted_start = schedule.start_date + timedelta(days=schedule.paused_days_total)

        # סגירה אחורה: cliff שנופל על 29/2 מתקיים ב-28/2 ולא ב-1/3. זהו תנאי תוכנית
        # (plan term) ולא הוראת מיסוי, והכיוון לטובת העובד ביום אחד.
        cliff_date = shift_months(adjusted_start, schedule.cliff_months, CLAMP_BACK)

        if target_date < cliff_date:
            return 0.0

        months_passed = DeterministicESOPEngine._completed_months(adjusted_start, target_date)
        if months_passed >= schedule.total_months:
            return float(grant.total_options)

        return round((grant.total_options / schedule.total_months) * months_passed, 2)

    @staticmethod
    def vesting_cutoff_date(employee: Employee, target_date: date) -> date:
        """התאריך שעד אליו ההבשלה נמשכת בפועל.

        הבשלה נעצרת ביום העזיבה - זו ההנחה שהמערכת כבר עבדה לפיה במקום אחד
        (החזרת האופציות שלא הבשילו לפול בעת פיטורים) אבל לא במקום השני: החישוב
        עצמו קיבל תמיד ``date.today()``, ולכן עובד שעזב המשיך "להבשיל" עוד ועוד
        אופציות אחרי שכבר לא עבד בחברה - וזה גם מה שהזיז את יתרות הפול מהמענקים.
        עובד בומרנג (ACTIVE עם termination_date היסטורי) אינו נעצר: הסטטוס קובע.
        """
        if (employee is not None
                and employee.status in (EmployeeStatus.TERMINATED, EmployeeStatus.DECEASED)
                and employee.termination_date):
            return min(target_date, employee.termination_date)
        return target_date

    @staticmethod
    def _completed_months(anchor: date, target: date) -> int:
        """מספר החודשים שהושלמו *במלואם* בין anchor ל-target.

        הפרש חודשים קלנדרי בלבד (השיטה הקודמת) זיכה חודש שלם ברגע שמספר החודש
        התחלף: מענק שהתחיל ב-15/1 קיבל חודש הבשלה כבר ב-1/2, כלומר 14 יום לפני
        שהחודש הזה חלף. בתחילת חודש (היום הנפוץ בדאטה) אין הפרש, ולכן זה לא
        התגלה - אבל לכל מענק שמתחיל באמצע החודש זה כסף שהובשל מוקדם מדי.
        """
        raw = (target.year - anchor.year) * 12 + (target.month - anchor.month)
        if raw <= 0:
            return max(0, raw)
        if target < shift_months(anchor, raw, CLAMP_BACK):
            raw -= 1
        return raw

    @staticmethod
    def check_trustee_holding_period(grant: Grant, check_date: date) -> tuple[bool, date]:
        """בדיקת שנתיים חסימה בנאמנות (סעיף 102)."""
        if not grant.trustee_deposit_date:
            return False, check_date

        # ROLL_FORWARD בכוונה, ובניגוד ל-cliff: הפקדה ב-29/2 מסתיימת ב-1/3 ולא
        # ב-28/2. זו תקופת חסימה סטטוטורית שמזכה במסלול רווח הון, וסגירה אחורה
        # הייתה מקצרת אותה ביום - כלומר מעניקה את הטבת המס יום אחד לפני הזמן.
        # ⚠️ זו בחירה שמרנית של המערכת (לא לתת הטבה מוקדם), לא כלל מס מאומת:
        # הפרשנות המחייבת ל-"24 חודשים מ-29/2" טרם אומתה מול מקור רשמי.
        end_date = shift_months(grant.trustee_deposit_date,
                                DeterministicESOPEngine.TRUSTEE_HOLDING_MONTHS,
                                ROLL_FORWARD)
        return check_date >= end_date, end_date

    @staticmethod
    def check_post_termination_exercise_window(grant: Grant, employee: Employee, check_date: date) -> tuple[bool, date]:
        """בדיקת חלון מימוש לאחר עזיבה. מחזיר (מותר_להגיש_בקשה, תאריך_דדליין_או_None).
        דדליין None כשאין הגבלה בכלל (העובד עדיין ACTIVE/ON_LEAVE - כולל מי שעזב וחזר
        ('בומרנג'), שם termination_date נשאר היסטורי גם שהעובד כרגע פעיל)."""
        if employee.status not in (EmployeeStatus.TERMINATED, EmployeeStatus.DECEASED):
            return True, None
        if not employee.termination_date:
            return True, None

        window_days = (
            DeterministicESOPEngine.POST_TERMINATION_WINDOW_DAYS_DEATH
            if employee.status == EmployeeStatus.DECEASED
            else grant.post_termination_window_days
        )
        deadline = employee.termination_date + timedelta(days=window_days)
        return check_date <= deadline, deadline
