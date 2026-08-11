"""התאמת ייבוא (v0.9.1 שלב ב, PLAN.md §8 שלב 9) - מריץ מחדש את מנוע ההבשלה
ואת מנוע המס על תוכן החבילה שיובאה, ומשווה לתוצאה שכבר נשמרה - לא רק ספירת
שורות. ראו PLAN.md §7 סיכון 1 ו-HANDOFF.md על שני ההכרעות הבאות (אושרו מול
המשתתף לפני המימוש):

**היקף בדיקת ההבשלה - השוואה ישירה וצרה, לא vesting_cutoff_date המלא.**
``calculate_vested_options`` (engine.py) הוא סטטי וטהור לפי (Grant,
VestingSchedule, target_date) בלבד - אין לו תלות ב-Employee/termination_date.
אובייקט Grant זמני שנבנה מה-bundle (ר' _deserialize_row למטה) אין לו קשר
session/relationship טעון, ולכן אי אפשר להריץ עליו את ``vesting_cutoff_date``
המלא כמו שהדשבורד עושה בפועל - זו בדיקה צרה יותר מ"האם המספר בדשבורד נכון",
במפורש: היא מוכיחה שה-Grant/VestingSchedule עצמם עברו את גבול הייבוא בשלמותם,
לא שכל שרשרת התלות של תצוגת ההבשלה למשתמש קיימת.

**השוואה אחידה, לא מודעת-סיווג.** commit() (task #7) לא שומר את סיווג
NEW/SKIP_EXISTING/ERROR של dry_run אחרי שהוא רץ - אחרי commit מוצלח, שורה
שיובאה החדש ושורה שכבר הייתה קיימת ביעד (decision D - skip-if-exists, לא
נדרסת) נראות זהות ב-DB. ההתאמה כאן משווה את כל מה שבחבילה מול היעד, בלי
ניסיון לשחזר איזו שורה הייתה NEW - זו הבחירה הפשוטה יותר, במחיר מוצהר:
הבדל שנמצא בשורה שהייתה כבר קיימת ביעד (decision D) עשוי לשקף שהיעד שימר
נתון קיים משלו, לא בהכרח באג ייבוא. ``known_limitations`` בדוח אומר את זה
במפורש (ר' ``reconcile`` למטה) - לא רק ב-copy של ה-UI, כדי שצרכן-API יידע גם
בלי לקרוא תיעוד.

**צד המס לא צריך את גוף ה-bundle בכלל, בכוונה.** ``ExerciseTaxRecord.gain``
(decision A, task #2) הוא הקלט הגולמי שכבר נשמר בפועל ביעד (מיובא כמו שהוא,
נשמר על אותו record_id) - ה"מקור" שיש להתאים אליו הוא בדיוק השורה הזו, לא
העתק נוסף בתוך ה-JSON. לכן ההתאמה קוראת ExerciseTaxRecord+ExerciseRequest
מה-DB (היעד) בלבד, ומריצה מחדש את ``TaxCalculationEngine.calculate_tax`` נגד
חבילות המס *של היעד* - זה בדיוק מה שתופס חבילת מס שהותאמה לא נכון לפי מפתח
טבעי בייבוא (import_.py, "בלי הפתרון הזה חישוב מס על היעד לא היה מוצא את
שורות הפירוט בכלל"). תאריך המימוש לחישוב נגזר מ-``business_date_of`` על
``ExerciseRequest.requested_at`` של היעד - בדיוק אותו כלל כמו באישור בקשה
אמיתית (decision B, task #2) - לעולם לא משעון היעד. ההשוואה היא לא רק
tax_amount: פאק מס "אחר" שבמקרה נותן אותו סכום עדיין ייתפס כי
table_effective_date/method/effective_rate מושווים גם הם (ממצא מומחה המס).

**אין סבילות עיגול.** שני הצדדים עוברים באותה פונקציה דטרמיניסטית
(round(...,2) בתוך שני המנועים) עם אותם קלטים מוצהרים (as_of/exercise_date
משותפים) - הבדל בפלט means הבדל אמיתי בקלט, לא רעש ייצוג בינארי. השוואת
שוויון מדויק היא הבדיקה הנכונה, לא סלחנית מדי.

**מחוץ להיקף במפורש, ומוצהר ב-known_limitations ולא רק מובלע**: יתרות
OptionPool/LedgerOwnership (הבשלה+מס בלבד כאן, לא ה-ledger) וסיווג
IL_102_CAPITAL_GAINS למענקים שעוגן חסימת הנאמנות שלהם קודם לתיקון 147
(01/01/2006) - הסכמה בין מקור ליעד שם משכפלת את אותה טעות ידועה
(TRUSTEE_HOLDING_MONTHS=24 גלובלי, ר' HANDOFF.md), לא מוכיחה נכונות.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from backend.app.models import ExerciseRequest, Grant, VestingSchedule
from backend.app.services.engine import DeterministicESOPEngine
from backend.app.services.import_ import _deserialize_row, _parse_date
from backend.app.services.tax_engine import MissingTaxRuleError, TaxCalculationEngine
from backend.app.types import business_date_of, business_today

KNOWN_LIMITATIONS = [
    "יתרות OptionPool/LedgerOwnership (ה-ledger) אינן בהיקף הדוח הזה - רק "
    "הבשלה (Grant+VestingSchedule) ומס (ExerciseTaxRecord) מושווים.",
    "מענקים שעוגן חסימת הנאמנות שלהם (max(grant_date, trustee_deposit_date)) "
    "קודם לתיקון 147 (01/01/2006) עשויים להראות התאמה מלאה בין מקור ליעד גם "
    "כשהסיווג עצמו שגוי (TRUSTEE_HOLDING_MONTHS=24 קבוע גלובלי) - הסכמה בין "
    "הצדדים אינה הוכחת נכונות עבורם.",
    "שורות שכבר היו קיימות ביעד לפני ייבוא זה (SKIP_EXISTING, decision D) "
    "מושוות כאן בדיוק כמו שורה חדשה; הבדל שנמצא בהן עשוי לשקף שהיעד שימר "
    "נתון קיים משלו, לא בהכרח באג ייבוא.",
]


@dataclass
class ReconciliationMismatch:
    entity_type: str  # "Grant" | "ExerciseTaxRecord"
    entity_id: str
    field_name: str
    source_value: object
    target_value: object
    reason: str


@dataclass
class ReconciliationReport:
    as_of: date
    grants_checked: int = 0
    exercises_checked: int = 0
    mismatches: List[ReconciliationMismatch] = field(default_factory=list)
    known_limitations: List[str] = field(default_factory=lambda: list(KNOWN_LIMITATIONS))

    @property
    def clean(self) -> bool:
        return not self.mismatches


def _reconcile_vesting(db: Session, bundle: dict, as_of: date) -> tuple[int, List[ReconciliationMismatch]]:
    mismatches: List[ReconciliationMismatch] = []
    schedules_by_grant: Dict[str, dict] = {
        row["grant_id"]: row for row in bundle["tables"].get("vesting_schedules", [])
    }
    grant_rows = bundle["tables"].get("grants", [])
    checked = 0

    for grant_row in grant_rows:
        grant_id = grant_row["grant_id"]
        schedule_row = schedules_by_grant.get(grant_id)
        if schedule_row is None:
            # אין לוח הבשלה בחבילה בכלל - אין מה להריץ מחדש (המנוע עצמו היה
            # זורק MissingVestingScheduleError); לא מסווג כאי-התאמה.
            continue
        checked += 1

        source_grant = Grant(**_deserialize_row(Grant, grant_row))
        source_schedule = VestingSchedule(**_deserialize_row(VestingSchedule, schedule_row))

        target_grant = db.get(Grant, grant_id)
        target_schedule = (
            db.query(VestingSchedule).filter(VestingSchedule.grant_id == grant_id).first()
        )
        if target_grant is None or target_schedule is None:
            mismatches.append(ReconciliationMismatch(
                "Grant", grant_id, "vested_options", source_value=None, target_value=None,
                reason="grant/vesting_schedule חסרים ביעד אחרי commit - לא אמור לקרות בכתיבה "
                       "all-or-nothing תקינה",
            ))
            continue

        # אין try/except MissingVestingScheduleError כאן בכוונה: שני התנאים
        # למעלה (schedule_row is None / target_schedule is None) כבר שוללים
        # את המצב היחיד שגורם לה - קוד הגנתי שלא יכול להתממש הוא קוד מת.
        source_vested = DeterministicESOPEngine.calculate_vested_options(
            source_grant, source_schedule, as_of)
        target_vested = DeterministicESOPEngine.calculate_vested_options(
            target_grant, target_schedule, as_of)

        if source_vested != target_vested:
            mismatches.append(ReconciliationMismatch(
                "Grant", grant_id, "vested_options",
                source_value=source_vested, target_value=target_vested,
                reason=f"הבשלה מחדש מהחבילה ({source_vested}) שונה מהבשלה מחדש ביעד "
                       f"({target_vested}) לתאריך {as_of.isoformat()}",
            ))

    return checked, mismatches


def _reconcile_tax(db: Session, bundle: dict) -> tuple[int, List[ReconciliationMismatch]]:
    mismatches: List[ReconciliationMismatch] = []
    record_rows = bundle["tables"].get("exercise_tax_records", [])
    checked = 0

    for record_row in record_rows:
        checked += 1
        record_id = record_row["record_id"]
        request_id = record_row["request_id"]

        target_request = (
            db.query(ExerciseRequest).filter(ExerciseRequest.request_id == request_id).first()
        )
        if target_request is None:
            mismatches.append(ReconciliationMismatch(
                "ExerciseTaxRecord", record_id, "tax_amount", source_value=None, target_value=None,
                reason=f"exercise_requests.{request_id} חסר ביעד אחרי commit - לא אמור לקרות "
                       "בכתיבה all-or-nothing תקינה",
            ))
            continue

        exercise_date = business_date_of(target_request.requested_at)
        stored_tax_amount = record_row["tax_amount"]
        stored_effective_rate = record_row["effective_rate"]
        stored_table_effective_date = _parse_date(record_row["effective_start_date"])
        stored_method = record_row["calculation_method"]

        try:
            result = TaxCalculationEngine.calculate_tax(
                db, record_row["country_code"], record_row["grant_type"], exercise_date,
                record_row["gain"],
            )
        except MissingTaxRuleError as e:
            mismatches.append(ReconciliationMismatch(
                "ExerciseTaxRecord", record_id, "tax_amount",
                source_value=stored_tax_amount, target_value=None,
                reason=f"חבילת מס לא נפתרת ביעד: {e}",
            ))
            continue

        # השוואה לפי שדה, לא רק tax_amount: שתי חבילות מס שונות יכולות
        # "במקרה" להסכים על הסכום הסופי ולחלוק שיעור/שיטה/תאריך תוקף שונים -
        # ממצא מומחה המס. כל שדה שסוטה מדווח בנפרד עם הערך שבו הוא בפועל
        # סטה, לא עם tax_amount קבוע מראש שעלול להטעות (שני הצדדים שווים בו).
        field_comparisons = (
            ("tax_amount", stored_tax_amount, result.tax_amount),
            ("effective_rate", stored_effective_rate, result.effective_rate),
            ("table_effective_date", stored_table_effective_date, result.table_effective_date),
            ("calculation_method", stored_method, result.method),
        )
        for field_name, source_value, target_value in field_comparisons:
            if source_value != target_value:
                mismatches.append(ReconciliationMismatch(
                    "ExerciseTaxRecord", record_id, field_name,
                    source_value=source_value, target_value=target_value,
                    reason=f"{field_name} מחושב מחדש ביעד ({target_value}) שונה מהרשומה השמורה "
                           f"({source_value})",
                ))

    return checked, mismatches


def reconcile(db: Session, bundle: dict, as_of: Optional[date] = None) -> ReconciliationReport:
    """נקודת הכניסה. ``bundle`` הוא ה-JSON השמור של ה-dry-run שעליו התבסס ה-
    commit (בדיוק כמו commit() עצמו - ר' import_.py) - לא נכתב כאן, רק נקרא
    ומושווה. as_of ברירת מחדל ל-business_today(): זו הרצה אבחנתית וניתנת
    לחזרה, לא תאריך-מס קפוא כמו exercise_date (שם השעון אסור - ר' decision B).
    """
    as_of = as_of or business_today()
    grants_checked, vesting_mismatches = _reconcile_vesting(db, bundle, as_of)
    exercises_checked, tax_mismatches = _reconcile_tax(db, bundle)
    return ReconciliationReport(
        as_of=as_of,
        grants_checked=grants_checked,
        exercises_checked=exercises_checked,
        mismatches=vesting_mismatches + tax_mismatches,
    )
