from datetime import date, timedelta
from backend.app.models import Grant, VestingSchedule, Employee, EmployeeStatus

class DeterministicESOPEngine:

    # חלון מורחב במקרה פטירה - נוהג מקובל בתוכניות אופציות (לרוב ~12 חודש), לא קבוע
    # בחוק כמו חסימת הנאמנות של 2 שנים; לכן קבוע כאן ולא ניתן להגדרה פר-מענק.
    POST_TERMINATION_WINDOW_DAYS_DEATH = 365

    @staticmethod
    def calculate_vested_options(grant: Grant, schedule: VestingSchedule, target_date: date) -> float:
        """חישוב קשיח בלתי תלוי ב-AI עבור הבשלת אופציות."""
        if not schedule:
            return 0.0

        adjusted_start = schedule.start_date + timedelta(days=schedule.paused_days_total)
        
        # חישוב Cliff ישיר (מביא לקריסה ב-29 בפברואר בשנה שאינה מעוברת)
        cliff_date = date(adjusted_start.year + (schedule.cliff_months // 12), 
                          adjusted_start.month, adjusted_start.day)
        
        if target_date < cliff_date:
            return 0.0

        months_passed = (target_date.year - adjusted_start.year) * 12 + (target_date.month - adjusted_start.month)
        if months_passed >= schedule.total_months:
            return float(grant.total_options)

        return round((grant.total_options / schedule.total_months) * months_passed, 2)

    @staticmethod
    def check_trustee_holding_period(grant: Grant, check_date: date) -> tuple[bool, date]:
        """בדיקת שנתיים חסימה בנאמנות (סעיף 102)."""
        if not grant.trustee_deposit_date:
            return False, check_date

        deposit = grant.trustee_deposit_date
        end_date = date(deposit.year + 2, deposit.month, deposit.day)
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