# QA_TESTBOOK — ספר בדיקות ומפת סיכונים

**המסמך החי של איכות המערכת.** הוא מחליף את `qa_bug_accounts.md` (שנמחק) ומתעדכן
**בכל גרסה**: כותרת חדשה + הבאגים והבדיקות של אותה גרסה. סגירת גרסה בלי עדכון כאן
היא גרסה לא גמורה — הכלל מופיע ב-`CLAUDE.md` תחת Definition of done.

## מבנה

לכל גרסה שני חלקים, בכוונה מופרדים:

- **(א) מקרי בדיקה** — מה להריץ, על מי, ומה התוצאה הצפויה. ניתן להרצה יד ביד.
- **(ב) אזורי סיכון** — חולשות **בקוד שקיים בפועל**: כללים לא מאומתים, נתיבים לא
  מכוסים, חוב מתוכנן. אין כאן ניחושים על פיצ'רים שטרם נבנו.

מזהי בדיקה: `QA-<גרסה>-<מספר>` (למשל `QA-050-03`). מזהה לא ממוחזר גם אחרי שהבדיקה
מתייתרת — כדי שדוח באג היסטורי יישאר בר-פענוח.
עמודת **כיסוי** מציינת אם קיימת בדיקה אוטומטית או שהבדיקה ידנית בלבד.

## הכנה להרצה — אל תבדוק על ה-DB החי

`esop_database.db` מחזיק נתוני עבודה. כל בדיקה שכותבת (אישור בקשה, dismiss, שינוי
העדפות) משנה אותו. הריצו סנדבוקס נפרד:

```bash
export PYTHONIOENCODING=utf-8
export ESOP_DATABASE_URL="sqlite:///./qa_sandbox.db"
python -m backend.seed_data
python -m uvicorn backend.app.main:app --port 8001
```

`PYTHONIOENCODING=utf-8` חובה ב-Windows: `seed_data` מדפיס אמוג'י, וב-cp1252 הוא
נופל ב-`UnicodeEncodeError` באמצע הזריעה.

**סיסמה אחידה לכל המשתמשים: `Demo1234!`** · חשבונות — נספח ב · הרצת הבדיקות
האוטומטיות: `pytest` משורש הפרויקט (מפנה את עצמו ל-DB זמני, ראו `tests/conftest.py`).

---

## דפוסי כשל שחזרו בפועל

חמש מחלקות שכל אחת ייצרה יותר מבאג אחד במערכת הזו. **בכל פיצ'ר חדש לבדוק את
החמישה** — זו הרשימה שהוכיחה את עצמה, לא רשימה תיאורטית.

| # | הדפוס | איפה זה כבר קרה |
|---|---|---|
| P1 | **חשבון תאריכים** — יום שלא קיים בחודש היעד, הפרש חודשים קלנדרי, חלוקה ב-12 | קריסות 29/2 (הבשלה + נאמנות), `cliff_months // 12`, זיכוי חודש 14 יום מוקדם |
| P2 | **סקופ הרשאות חסר** — הרשומה נשלפת לפי מזהה מהלקוח בלי לבדוק בעלות | `admin/employees`, `employee/dashboard/{id}`, `simulate-exercise` |
| P3 | **ולידציה קיימת בנתיב אחד וחסרה בשני** | אישור בקשת מימוש: כל הבדיקות חסרו גם ב-admin וגם ב-trustee |
| P4 | **נתון חסר מוצג כערך עסקי** — `None` שהופך ל-0 | מענק בלי `VestingSchedule` הציג `vested=0` לצמיתות |
| P5 | **כתיבה לא אידמפוטנטית** — אותה פעולה פעמיים משנה מצב פעמיים | החזרת אופציות לפול בפיטורים, אישור חוזר של בקשה שכבר טופלה |
| P6 | **נאמנות טיפוסים בין כתיבה לקריאה** — JSON אין לו סוג תאריך; בלי פענוח מפורש, תאריך חוזר כמחרוזת ולא כ-`date` | `project()` על שדה תאריך, ראו v0.6.0 למטה |

---

# v0.6.0 — Ledger מבוסס-אירועים ושחזור בי-טמפורלי (שלבים 1–4 מתוך 4 — הושלם)

**מטרה:** מצב עסקי הופך מ"שדה שמישהו עורך" ל*תוצר חישוב* מרצף אירועים
append-only. קריטריונים 1–3 ב-`GOAL.md`. שלב 1: סכמה, מיגרציה, גיבוי
(backfill), ושכבת קיפול (fold). שלב 2: חיווט חמש נקודות המוטציה החיות, איחוד
שני נתיבי אישור בקשת המימוש, ותיקון backdating בהפקדת נאמן. שלב 3: שכבת
השאילתה הבי-טמפורלית + שני מסכים ב-admin portal (ציר זמן, שחזור לתאריך) -
דרך א' שהוחלטה: הבוס הקיים (`COMPANY_ADMIN`) מקבל גישה, בלי תפקיד "מבקר" חדש.
**שלב 4 (אחרון)**: פיצ'ר הקפאת הבשלה (leave-of-absence) - endpoint חדש
שכותב ל-`VestingSchedule.paused_days_total` דרך הלדג'ר, וכפתור "הקפאה" חדש
ליד "ציר זמן" בטבלת המענקים ב-admin portal.

## (א) מקרי בדיקה — שלב 1

| מזהה | מה בודקים | איך | תוצאה צפויה | כיסוי |
|---|---|---|---|---|
| QA-060-01 | **Replay-equivalence על כל הדאטה האמיתי** | גיבוי + קיפול על עותק מלא של `esop_database.db` (19 פולים, 260 עובדים, 251 מענקים, 247 לוחות הבשלה, 4 בקשות) | 0 אי-התאמות מול העמודות המוטטות בפועל | אומת ידנית מול עותק חי — ראו `test_replay_equivalence_for_every_aggregate_type` לגרסה הממוסמכת ב-pytest |
| QA-060-02 | קיפול פול: בסיס + שתי דלתאות | `POOL_BALANCE_ESTABLISHED` ואז `POOL_ALLOCATED`/`POOL_UNVEST_RETURNED` | סכום נכון של allocated/unallocated | `test_pool_projection_folds_established_then_deltas` |
| QA-060-03 | קיפול עובד: שינוי סטטוס דורס בסיס | `EMPLOYEE_STATE_ESTABLISHED` ואז `EMPLOYEE_STATUS_CHANGED` | status/termination_date מהאירוע האחרון | `test_employee_projection_terminated_overrides_established` |
| QA-060-04 | קיפול מענק: הפקדת נאמן אחרי יצירה | `GRANT_CREATED` ואז `TRUSTEE_DEPOSIT_CONFIRMED` | `trustee_deposit_date` מעודכן, כ-`date` ולא מחרוזת | `test_grant_projection_deposit_confirmed_after_creation` |
| QA-060-05 | ישות בלי אירועים בכלל | `project_option_pool([])` | `None`, לא קריסה ולא 0 | `test_missing_aggregate_projects_to_none` |
| QA-060-06 | `append_event` דוחה סוג אירוע/צובר לא מוכר | `event_type`/`aggregate_type` לא ב-`LEDGER_EVENT_TYPES`/`LEDGER_AGGREGATE_TYPES` | `UnknownLedgerEventType`/`UnknownLedgerAggregateType` | `test_append_event_rejects_unknown_event_type`, `test_append_event_rejects_unknown_aggregate_type` |
| QA-060-07 | רצף (sequence_no) עולה לפי ישות | שני אירועים על אותה `aggregate_id` | `1, 2` בסדר | `test_append_event_assigns_increasing_sequence_per_aggregate` |
| QA-060-08 | `LedgerOwnership` נקבע פעם אחת, immutable | קריאה שנייה ל-`record_ownership` עם ערך אחר | הערך הראשון נשאר | `test_record_ownership_is_set_once_and_immutable` |
| QA-060-09 | **הגנת שינוי אמיתית** — UPDATE/DELETE נדחים ברמת ה-DB | טריגרים שנבנים בבדיקה עצמה (create_all לא מריץ CREATE TRIGGER) | `IntegrityError` על שני הניסיונות | `test_ledger_events_reject_update_at_the_db_level`, `test_ledger_events_reject_delete_at_the_db_level` — **אומת גם ידנית** מול עותק חי עם `sqlite3` גולמי |
| QA-060-10 | שאילתה על ידיעה שלפני רגע הגיבוי | `as_of_knowledge_date` לפני `run_at` | `None` — לא מתחזה לידע שאין | `test_query_before_backfill_knowledge_date_returns_no_history` |
| QA-060-11 | **דוגמה מחושבת ביד: bitemporal אמיתי** | הפקדת נאמן 20 יום אחרי יצירת מענק; קיפול לפני/אחרי | לפני ההפקדה: `None`; אחריה: `2021-01-20` — שתי תשובות נכונות לאותה שאלה | `test_bitemporal_query_before_and_after_deposit_confirmation_differ` |
| QA-060-12 | גיבוי לא רץ פעמיים | `python -m backend.backfill_ledger` פעם שנייה | מסרב, לא כותב כפילויות | אומת ידנית מול עותק חי |

## (ב) אזורי סיכון — שלב 1

| מזהה | הסיכון | למה | מה לבדוק |
|---|---|---|---|
| R-060-01 | **`sequence_no` מניח כותב יחיד** | `_next_sequence_no` הוא query+increment בלי נעילה - שני writers מקבילים על אותה ישות עלולים להתנגש. SQLite חד-תהליכי מקטין את הסיכון אבל לא מבטל אותו | אם אי-פעם יתווסף כותב מקביל אמיתי (worker נפרד) - לחזק ל-transaction עם retry, לא רק constraint |
| R-060-02 | **נאמנות טיפוסים JSON↔Python** | תאריך שנכתב כ-ISO string וחוזר כמחרוזת בלי `_parse_date` מפורש - **נתפס בפועל** באמצע הפיתוח (`test_replay_equivalence_for_every_aggregate_type` נכשל על `termination_date`), לא רק תיאורטי | כל שדה תאריך חדש בפרויקטור עתידי (שלב 3+) חייב לעבור דרך `_parse_date` באותה צורה |
| R-060-03 | `ledger_events`/`ledger_ownership` לא קיימים בסכמת ה-`create_all` של הבדיקות עם הטריגרים | `Base.metadata.create_all` יוצר את הטבלאות (דרך המודלים) אבל **לא** את ה-triggers (הם raw SQL במיגרציה בלבד) - QA-060-09 בונה אותם ידנית בבדיקה עצמה | אם המיגרציה תשתנה, לוודא שהבדיקה עדיין תואמת את ה-SQL בפועל |
| R-060-04 | סדר הכנסה (flush) בין `Grant` ל-`ExerciseRequest` לא תמיד נכון כשמצרפים הרבה אובייקטים תלויים בפלאש אחד | אין `relationship()` מוצהר בין השניים ב-`models.py` - עובדה קיימת בסכמה, לא באג חדש. **נתפס בפועל** בזמן כתיבת `seeded_world` fixture | fixtures עתידיים שמכניסים דאטה תלוי דרך כמה טבלאות צריכים פלאש ביניים, לא פלאש אחד בסוף |
| R-060-05 | **גיבוי לא משחזר היסטוריית ביניים אמיתית** — `POOL_BALANCE_ESTABLISHED` הוא snapshot של המצב הנוכחי, לא רצף האירועים האמיתי שהוביל אליו | אי אפשר לשחזר את זה מ-`AuditLog` (JSON טקסטואלי, לא מובטח שלם) - הוחלט במפורש לא לנסות | שאילתת "מה חשבנו" לפני רגע הגיבוי חייבת להחזיר "אין נתון" (QA-060-10), לא לנחש היסטוריה שאין |
| R-060-06 | ~~חיווט חמש נקודות המוטציה החיות עדיין לא קיים~~ | **נסגר בשלב 2** — ראו למטה | — |

## (א) מקרי בדיקה — שלב 2

הבדיקות עוברות דרך ה-API האמיתי (`client` fixture), לא קוראות ל-`append_event`
ישירות — כדי להוכיח שהחיווט ב-`routes.py` עובד, לא רק שהשירות עצמו עובד.

| מזהה | מה בודקים | איך | תוצאה צפויה | כיסוי |
|---|---|---|---|---|
| QA-060-20 | `create_grant` מייצר שלושה אירועי בסיס | `POST /admin/grants` | `GRANT_CREATED` + `POOL_ALLOCATED` + `VESTING_SCHEDULE_ESTABLISHED`, ושלושתם ברשומת `LedgerOwnership` נכונה | `test_create_grant_appends_baseline_events_and_ownership` |
| QA-060-21 | פרויקציית הפול נכונה אחרי כמה מענקים | שני `POST /admin/grants` על אותו פול | `project()` == עמודות הפול בפועל | `test_create_grant_projection_matches_pool_after_two_grants` |
| QA-060-22 | **תיקון באג אמיתי**: מענק עם `grant_date` ישן מ-`pool.created_at` | מענק עם תאריך מ-2015 מול פול שנוצר "עכשיו" | `POOL_ALLOCATED` מקופל נכון ולא מתעלם | `test_pool_projection_still_correct_when_grant_predates_pool_row_creation` — **נמצא ותוקן באימות ידני מול עותק חי**, ראו R-060-07 |
| QA-060-23 | עזיבה (legacy status endpoint) עם החזרה לפול | `PATCH /admin/employees/{id}/status`, `TERMINATED`+`return_unvested_to_pool` | `EMPLOYEE_STATUS_CHANGED` + `POOL_UNVEST_RETURNED` אחד; `project()` תואם | `test_employee_termination_appends_status_event_and_pool_return_events` |
| QA-060-24 | עזיבה כפולה לא מכפילה אירועי החזרה | אותה קריאה פעמיים | אירוע `POOL_UNVEST_RETURNED` **אחד** בלבד; `EMPLOYEE_STATUS_CHANGED` נרשם בכל פעם | `test_terminating_twice_does_not_duplicate_pool_return_events` |
| QA-060-25 | מחיקה רכה (soft-delete) מייצרת אירוע סטטוס משלה | `DELETE /admin/employees/{id}` עם היסטוריית מענקים | `EMPLOYEE_STATUS_CHANGED` אחד — נתיב שלא עובר דרך `update_employee_status` | `test_soft_delete_appends_status_event_too` |
| QA-060-26 | עובד חדש מקבל אירוע בסיס | `POST /admin/employees` | `EMPLOYEE_STATE_ESTABLISHED`; `project()` == `{status: ACTIVE, termination_date: None}` | `test_create_employee_appends_baseline_event` |
| QA-060-27 | בקשת מימוש חדשה מקבלת אירוע בסיס | `POST /employee/exercise-requests` | `EXERCISE_REQUEST_SUBMITTED` | `test_create_exercise_request_appends_baseline_event` |
| QA-060-28 | אישור admin מייצר `EXERCISE_REQUEST_DECIDED` | `PATCH /admin/exercise-requests/{id}`, `approve=true` | רצף `SUBMITTED→DECIDED`; `project()["status"]=="APPROVED"` | `test_admin_approval_appends_decided_event` |
| QA-060-29 | דחיית נאמן — **אותה נקודת כתיבה** כמו admin | `PATCH /trustee/exercise-requests/{id}`, `approve=false` | `EXERCISE_REQUEST_DECIDED` יחיד; `project()["status"]=="REJECTED"` | `test_trustee_rejection_appends_decided_event_via_the_same_shared_function` |
| QA-060-30 | הפקדת נאמן מייצרת אירוע | `PATCH /trustee/confirm-deposit/{id}` | `TRUSTEE_DEPOSIT_CONFIRMED`; `project()` תואם `grant.trustee_deposit_date` | `test_confirm_deposit_appends_event` |
| QA-060-31 | **תיקון backdating**: הפקדה לפני תאריך המענק נחסמת | `deposit_date < grant.grant_date` | `400` · `"...cannot precede the grant date..."`; **אין** אירוע נכתב | `test_confirm_deposit_before_grant_date_is_rejected` |
| QA-060-32 | גבול הבדיקה: הפקדה *באותו* תאריך של המענק מותרת | `deposit_date == grant.grant_date` | `200` | `test_confirm_deposit_on_the_grant_date_itself_is_allowed` |

## (ב) אזורי סיכון — שלב 2

| מזהה | הסיכון | למה | מה לבדוק |
|---|---|---|---|
| R-060-07 | **`POOL_BALANCE_ESTABLISHED` עם `effective_date` שגוי — תוקן** | הגרסה המקורית (שלב 1) השתמשה ב-`pool.created_at.date()`: זמן יצירת השורה ב-DB, לא עובדה היסטורית. מענק חי עם `grant_date` ישן ממנו (המצב הנפוץ) "הקדים" את הבסיס בקיפול, וה-`POOL_ALLOCATED` שלו התעלם בשקט - **נמצא באימות ידני מול עותק חי**, לא בבדיקה. תוקן ל-`LEDGER_EPOCH` (`date.min`) - הבסיס תמיד ראשון | `test_pool_projection_still_correct_when_grant_predates_pool_row_creation` (QA-060-22) הוא בדיקת הרגרסיה |
| R-060-08 | אין endpoint ל-`OptionPool` חדש | `create_grant` מריץ `record_ownership` הגנתי לפול, אבל אם הפול לא קיים ב-`ledger_ownership`/ללא `POOL_BALANCE_ESTABLISHED` (למשל DB חדש לגמרי בלי גיבוי), `project()` יחזיר `None` ו-`POOL_ALLOCATED` יתעלם בשקט - בדיוק R-060-07 מחדש, בהקשר אחר | להריץ `backfill_ledger.py` **תמיד** לפני live traffic ראשון בסביבה חדשה; לתעד את סדר הפעולות (מיגרציה → גיבוי → שרת) |
| R-060-09 | ולידציית ה-backdating בודקת רק `deposit_date >= grant.grant_date` | ⚠️ אין אימות חיצוני לכלל הזה - זו הכרעת מערכת שמרנית (למנוע ניצול מוקדם של מסלול רווח הון), לא כלל מס מאומת. ראו החלטת האבטחה בתכנון v0.6.0 | אם יתגלה כלל רשמי אחר - לעדכן כאן ובקוד יחד |

## (א) מקרי בדיקה — שלב 3

| מזהה | מה בודקים | איך | תוצאה צפויה | כיסוי |
|---|---|---|---|---|
| QA-060-40 | ציר הזמן מחזיר אירועים בסדר, עם payload מפוענח | `GET /admin/ledger/Grant/{id}/events` | רשימת אירועים ממוינת; `payload` הוא `dict`, לא מחרוזת JSON גולמית | `test_timeline_returns_events_in_order_with_parsed_payload` |
| QA-060-41 | `aggregate_type` לא מוכר נדחה | `GET .../ledger/NotAThing/{id}/events` | `400` · `Unsupported aggregate_type` | `test_timeline_rejects_unknown_aggregate_type` |
| QA-060-42 | **IDOR חוצה-חברות נחסם** | אדמין של חברה B מבקש ציר זמן של מענק בחברה A | `403` | `test_timeline_blocks_cross_company_access` — **אומת גם ידנית בדפדפן** |
| QA-060-43 | ישות שלא קיימת ב-`ledger_ownership` בכלל | `aggregate_id` שאינו קיים | `403`, לא `200` עם רשימה ריקה (כדי לא להבחין בין "קיים אבל לא שלי" ל"לא קיים") | `test_timeline_for_unknown_aggregate_id_is_also_blocked_not_leaked_as_empty` |
| QA-060-44 | `as-of` בלי פרמטרים = המצב הנוכחי | `GET .../as-of` | תואם את `grant.trustee_deposit_date` בפועל | `test_as_of_with_no_params_returns_current_state` — **אומת בדפדפן** |
| QA-060-45 | **דוגמה מחושבת ביד, חיה**: יום לפני הפקדה | `effective_date` יום לפני `trustee_deposit_date` | `trustee_deposit_date: null` | `test_as_of_effective_date_before_deposit_shows_no_deposit_yet` — **אומת בדפדפן**: 2021-01-20→`null` |
| QA-060-46 | אותה דוגמה, ביום ההפקדה עצמו | `effective_date == trustee_deposit_date` | `trustee_deposit_date` מוצג | `test_as_of_effective_date_on_and_after_deposit_shows_it` — **אומת בדפדפן**: 2021-02-01→`"2021-02-01"` |
| QA-060-47 | שאילתת ידיעה לפני שהמענק בכלל נוצר | `knowledge_date` 10 שנים אחורה | `state: null` — "אין נתון", לא מתחזה | `test_as_of_knowledge_date_before_backfill_or_creation_returns_no_data` — **אומת בדפדפן** |
| QA-060-48 | `as-of` דוחה `aggregate_type` לא מוכר | כמו QA-060-41 | `400` | `test_as_of_rejects_unknown_aggregate_type` |
| QA-060-49 | `as-of` חוסם חוצה-חברות | כמו QA-060-42 | `403` | `test_as_of_blocks_cross_company_access` |
| QA-060-50 | **תיקון מ-change-reviewer**: תפקידי TRUSTEE/EMPLOYEE נחסמים בפועל מציר הזמן, לא רק לפי קריאת קוד | `GET .../events` עם טוקן TRUSTEE ואז EMPLOYEE | `403` בשני המקרים | `test_timeline_rejects_non_admin_roles` (parametrized) |
| QA-060-51 | אותו דבר עבור `as-of` | `GET .../as-of` עם טוקן TRUSTEE ואז EMPLOYEE | `403` בשני המקרים | `test_as_of_rejects_non_admin_roles` (parametrized) |
| QA-060-52 | **תיקון באג אמיתי מ-change-reviewer**: `aggregate_type` לא תואם ל-`aggregate_type` האמיתי שנשמר, אותה חברה | מענק אמיתי (aggregate_type="Grant") מבוקש דרך `GET /admin/ledger/Employee/{grant_id}/events` | `403` — לא עובר בשקט מול פרויקטור לא נכון | `test_timeline_rejects_mismatched_aggregate_type_even_same_company` |
| QA-060-53 | אותו דבר עבור `as-of` | כמו QA-060-52, על `/as-of` | `403` | `test_as_of_rejects_mismatched_aggregate_type_even_same_company` |

## (ב) אזורי סיכון — שלב 3

| מזהה | הסיכון | למה | מה לבדוק |
|---|---|---|---|
| R-060-10 | ~~אימות ההרשאה במסכי v0.6.0 בדק רק `company_id`, לא `aggregate_type`~~ **נסגר** | `_assert_ledger_ownership` עבר דרך `LedgerOwnership` (לא `project()`) מההתחלה - זו הייתה ההגנה הנכונה מפני IDOR. אבל **נמצא ב-change-reviewer**: מזהה תקין של ישות אחת (למשל מענק) עם `aggregate_type` של ישות אחרת מאותה חברה היה עובר את הבדיקה ומופעל מול הפרויקטור הלא נכון. תוקן: הבדיקה כוללת עכשיו גם `ownership.aggregate_type == aggregate_type` | QA-060-52/53 הן בדיקות הרגרסיה |
| R-060-11 | ה-UI מציג `payload` גולמי (JSON) בציר הזמן, לא מנוסח לפי סוג אירוע | מספיק לשלב 3 (שקוף, אמין) אבל לא "יפה" - שיפור עתידי אפשרי, לא חוסם | אין השפעה על נכונות, רק על חוויית משתמש |
| R-060-12 | `knowledge_date` מהדפדפן מגיע כ-`type="date"` (יום בלבד), בלי שעה | תואם למה שה-API כבר תומך בו (Pydantic הופך תאריך בלי שעה לחצות), אבל מגביל את הדיוק שהמשתמש יכול לבדוק בו דרך ה-UI ל-24 שעות | אם יידרש דיוק לשעה - להוסיף שדה שעה נפרד, לא לשנות את ה-API |

## (א) מקרי בדיקה — שלב 4

הקפאת הבשלה נרשמת כפעולה **רטרוספקטיבית אחת** (`start_date`+`end_date` ידועים
יחד) ולא כמצב "פתוח"/"סגור" בשני שלבים - שום דבר אחר במערכת לא עוקב אחרי
תקופת חופשה שעדיין לא נסגרה, בדיוק כמו `trustee_deposit_date` (גם הוא נרשם
בדיעבד). ראו GOAL.md: לא בונים state בלי צרכן.

| מזהה | מה בודקים | איך | תוצאה צפויה | כיסוי |
|---|---|---|---|---|
| QA-060-60 | רישום הקפאה מוסיף אירוע ומעדכן עמודה | `POST /admin/grants/{id}/vesting-pause` | `VESTING_PAUSE_RECORDED` אחד; `schedule.paused_days_total` == `days_added`; `project()` תואם לעמודה | `test_recording_a_pause_appends_event_and_updates_column` — **אומת גם בדפדפן**: G-2021-001, 2021-03-01→2021-04-30 (60 יום), טוסט הצגה נכון |
| QA-060-61 | שתי הקפאות לא-חופפות מצטברות | שתי קריאות רצופות | `paused_days_total` השני == סכום שני ה-`days_added` | `test_two_non_overlapping_pauses_accumulate` |
| QA-060-62 | `end_date <= start_date` נדחה | `end_date` לפני/שווה ל-`start_date` | `400` · `"end_date must be after start_date"` | `test_end_date_before_start_date_is_rejected` |
| QA-060-63 | הקפאה באורך אפס (אותו תאריך) נדחית | `start_date == end_date` | `400` | `test_zero_length_pause_is_rejected` |
| QA-060-64 | חפיפה עם הקפאה קיימת נחסמת | תקופה שנייה חופפת לראשונה | `400` · `"Overlaps an existing pause period..."`; **אין** אירוע נוסף נכתב (הבדיקה לפני הכתיבה, לא אחריה) | `test_overlapping_pause_period_is_rejected` |
| QA-060-65 | גבול הבדיקה: תקופה **צמודה** (לא חופפת בפועל) מותרת | תקופה שנייה מתחילה בדיוק ביום שהראשונה נגמרת | `200` | `test_adjacent_non_overlapping_pause_is_allowed` |
| QA-060-66 | מענק בלי לוח הבשלה בכלל | לוח הבשלה נמחק ידנית לפני הקריאה | `409` | `test_grant_without_vesting_schedule_returns_409` |
| QA-060-67 | **IDOR חוצה-חברות נחסם** | אדמין של חברה B מנסה להקפיא מענק בחברה A | `403` | `test_cross_company_grant_is_blocked` |
| QA-060-68 | מענק לא קיים | `grant_id` שאינו קיים | `404` | `test_unknown_grant_returns_404` |
| QA-060-69 | **החישוב בפועל משתנה, לא רק העמודה** | הקפאה של 60 יום נרשמת, ואז `DeterministicESOPEngine.calculate_vested_options` נקרא שוב לאותו תאריך בדיקה | ההבשלה המחושבת **קטנה** אחרי ההקפאה (ה-cliff נדחה) - לא בדיקת מתמטיקה חדשה, רק שהעמודה שה-endpoint מעדכן היא בדיוק מה שהמנוע הקיים כבר קורא | `test_pause_actually_shifts_the_cliff_in_the_existing_engine` |
| QA-060-70 | **תיקון מ-change-reviewer**: תפקידי TRUSTEE/EMPLOYEE נחסמים בפועל מהקפאת הבשלה, לא רק לפי קריאת קוד | `POST /vesting-pause` עם טוקן TRUSTEE ואז EMPLOYEE | `403` בשני המקרים | `test_non_admin_roles_are_rejected` (parametrized) |

## (ב) אזורי סיכון — שלב 4

| מזהה | הסיכון | למה | מה לבדוק |
|---|---|---|---|
| R-060-13 | חפיפה נבדקת רק מול הקפאות **קיימות באותו לוח הבשלה**, לא מול טווחי תאריכים אחרים על אותו עובד/מענק | `events_for(db, schedule.schedule_id)` מסונן לפי `aggregate_id` יחיד - זה בדיוק ההיקף הנכון (הקפאה שייכת ללוח הבשלה אחד), לא באג | אם אי-פעם יתווסף מושג "הקפאה חוצת-מענקים" לאותו עובד - זו נקודת ההרחבה |
| R-060-14 | אין הגבלה עסקית על סך ימי ההקפאה המצטברים (`paused_days_total`) ביחס לאורך לוח ההבשלה כולו | הכרעת מערכת שמרנית: המנוע הדטרמיניסטי כבר מטפל נכון בכל ערך חיובי (דוחה את ה-cliff/ההבשלה בהתאם), ואין כלל מס שמגביל את זה - לא הומצא כלל שלא קיים | אם יתגלה כלל רשמי (למשל תקרת חופשה מוכרת) - להוסיף ולידציה כאן ובקוד יחד |
| R-060-15 | ה-endpoint לא מייצר `POOL_UNVEST_RETURNED` או משנה יתרות פול | נכון בכוונה: הקפאה רק דוחה את לוח הזמנים, לא מבטלת אופציות שכבר הוקצו - שונה במהותו מעזיבת עובד (QA-060-23) | אין השפעה על יתרת הפול; אם ההנחה הזו תשתנה בעתיד (למשל הקפאה ארוכה מספיק שמבטלת חלק מהמענק) - זו החלטת מוצר חדשה, לא הרחבה טכנית |
| R-060-16 | ~~UI: `LEDGER_EVENT_LABELS` הציג מחרוזת גולמית `VESTING_PAUSE_RECORDED` במקום תווית עברית~~ **נסגר** | המפה עדיין הכילה את שני המפתחות הישנים `VESTING_PAUSE_STARTED`/`VESTING_PAUSE_ENDED` מהתכנון הדו-שלבי שנדחה בתכנון שלב 4, בלי מפתח לאירוע היחיד שבאמת נכתב - **נמצא ב-change-reviewer**, לא בבדיקה אוטומטית (זה UI טקסטואלי בלבד) | תוקן ל-`VESTING_PAUSE_RECORDED: "הקפאת הבשלה נרשמה"`; אין בדיקה אוטומטית לתוכן התווית (ראו R-060-11) |

---

# v0.5.1 — patch אבטחה

הושלם. ארבעה תיקונים בלתי תלויים שהוקדמו מ-v1.3.0 (ראו הערה ב-`FEATURE_SPEC.md`):
CORS מוגבל, ביטול הסיסמה הקבועה, נעילת חשבון, ניקוי session-ים שפגו.

## (א) מקרי בדיקה

### CORS

| מזהה | מה בודקים | איך | תוצאה צפויה | כיסוי |
|---|---|---|---|---|
| QA-051-01 | מקור לא מורשה לא מקבל את ה-header | `GET /version` עם `Origin: https://evil.example` | הבקשה עצמה מצליחה (200) אבל **בלי** `access-control-allow-origin` - כך דפדפן חוסם את הקריאה בצד הלקוח | `test_wildcard_origin_is_no_longer_allowed` |
| QA-051-02 | מקור מורשה מקבל את ה-header בחזרה | אותו endpoint עם `Origin` מתוך `ALLOWED_ORIGINS` | `access-control-allow-origin` שווה למקור שנשלח | `test_allowed_origin_gets_the_cors_header_back` |
| QA-051-03 | Preflight למקור לא מורשה נדחה | `OPTIONS /auth/login` עם `Access-Control-Request-Method: POST` ומקור זר | בלי `access-control-allow-origin` | `test_preflight_for_disallowed_origin_is_rejected` |
| QA-051-04 | `allow_credentials` כבוי | כל תשובה, כל מקור | `access-control-allow-credentials` לא `true` - האימות כולו Bearer, בלי עוגיות | `test_credentials_flag_is_off` |

### ביטול הסיסמה הקבועה + אכיפת החלפה

| מזהה | מה בודקים | איך | תוצאה צפויה | כיסוי |
|---|---|---|---|---|
| QA-051-05 | עובד חדש מקבל סיסמה מוגרלת, לא `Welcome123!` | `POST /admin/employees` | תגובה עם `temporary_password` (14 תווים, שונה מהקבוע הישן); `must_change_password=true` ב-DB | `test_new_employee_gets_a_random_password_not_welcome123` |
| QA-051-06 | חשבון עם must_change_password חסום מ-endpoints עסקיים | login עם הסיסמה החד-פעמית, ואז `GET /employee/dashboard/{id}` | `403` · `"...change-password"` | `test_new_employee_account_is_blocked_from_business_endpoints_until_password_change` — **אומת גם בדפדפן**: fetch ישיר החזיר 403 גם כשה-UI לא היה חוסם |
| QA-051-07 | `POST /auth/change-password` מסיר את החסימה | להחליף סיסמה ואז לגשת לאותו endpoint | `200`; הגישה נפתחת; login חוזר מראה `must_change_password=false` | `test_change_password_clears_the_flag_and_unblocks_access` — **אומת בדפדפן** מקצה לקצה: מסך "יש להחליף סיסמה" → main-app + פעמון ההתראות |
| QA-051-08 | סיסמה נוכחית שגויה נדחית | `current_password` שגוי | `401` | `test_change_password_rejects_wrong_current_password` |
| QA-051-09 | סיסמה קצרה מדי / זהה לישנה נדחית | `new_password` קצר מ-8, או זהה ל-`current_password` | `400` בשני המקרים | `test_change_password_rejects_too_short_or_identical` |
| QA-051-10 | שינוי סיסמה מבטל session-ים אחרים, לא את הנוכחי | שני logins, שינוי מה-session השני | ה-session שביצע את השינוי נשאר תקף (`/auth/me` 200); ה-session האחר מבוטל (401) | `test_change_password_invalidates_other_sessions_but_not_the_current_one` |
| QA-051-11 | פורטל האדמין מציג את הסיסמה החד-פעמית פעם אחת | יצירת עובד ב-UI | מודאל עם הסיסמה בפועל (לא toast חולף); `submitEmployeeModal` כבר לא מכיל `Welcome123!` מקודד | ידני — אומת בדפדפן (`temp-password-modal`, 14 תווים) |
| QA-051-12 | חשבונות QA שנזרעים ישירות לא נפגעים | `seed_data.py` יוצר משתמשים בלי דרך `/admin/employees` | `must_change_password=false`; `Demo1234!` ממשיך לעבוד בכל ספר הבדיקות | `test_seeded_qa_style_account_is_not_forced_to_change_password` |

### נעילת חשבון

| מזהה | מה בודקים | איך | תוצאה צפויה | כיסוי |
|---|---|---|---|---|
| QA-051-13 | נעילה אחרי `MAX_FAILED_LOGIN_ATTEMPTS` (5) כשלונות | 5 ניסיונות עם סיסמה שגויה, ואז ניסיון עם הסיסמה **הנכונה** | הניסיון ה-6 (גם עם סיסמה נכונה) מחזיר `423` · `"...locked..."` | `test_account_locks_after_max_failed_attempts`, `test_locked_account_rejects_the_correct_password_too` |
| QA-051-14 | login מוצלח מאפס את המונה | 4 כשלונות ואז הצלחה | `failed_login_attempts=0`, `locked_until=None` | `test_successful_login_resets_the_failed_attempt_counter` |
| QA-051-15 | `is_account_locked` תלוי בזמן | `locked_until` בעתיד מול בעבר | `True` / `False` בהתאמה | `test_locked_out_flag_helper` |

### ניקוי session-ים שפגו

| מזהה | מה בודקים | איך | תוצאה צפויה | כיסוי |
|---|---|---|---|---|
| QA-051-16 | session שפג נמחק בכל login | session עם `expires_at` בעבר + session תקף, ואז login כלשהו | הפג נמחק; התקף נשאר | `test_login_cleans_up_expired_sessions` |

## (ב) אזורי סיכון

| מזהה | הסיכון | למה | מה לבדוק |
|---|---|---|---|
| R-051-01 | הנעילה היא per-username, לא per-IP | תוקף שמפזר ניסיונות בין הרבה שמות משתמש לא נעצר. זו הגנה על חשבון בודד, לא rate limiting ברמת רשת - זה דורש תשתית (reverse proxy/Redis) שאין בפרויקט | אם נוסף IP tracking - לוודא שהוא לא מחליף את נעילת החשבון, רק מוסיף עליה |
| R-051-02 | `require_roles` חוסם את must_change_password, אבל `/search` ו-`/notifications` עוברים דרך `get_current_user` בלבד | חשבון עם סיסמה חד-פעמית עדיין יכול לחפש ולראות התראות לפני שהחליף סיסמה | להחליט אם זה מקובל (הן read-only) או להרחיב את החסימה |
| R-051-03 | מיגרציית ה-Alembic דרשה שני תיקונים ידניים מעבר ל-autogenerate | (1) `server_default` על עמודות NOT NULL - בלעדיו `ALTER TABLE` נכשל על DB עם משתמשים קיימים; (2) `op.add_column` ישיר במקום `batch_alter_table` - ה-batch גורם ל-SQLite לשחזר את הטבלה ולהיכשל על FK מטבלאות אחרות (`user_sessions`, `audit_log` וכו'). שניהם נבדקו בפועל מול עותק של `esop_database.db` (55 משתמשים) | כל מיגרציה עתידית שמוסיפה עמודת NOT NULL ל-`users` (או לכל טבלה עם תלויי-FK) צריכה לחזור על שתי הבדיקות האלה, לא לסמוך על הפלט הגולמי של autogenerate |
| R-051-04 | ה-downgrade של המיגרציה מכבה ומדליק `PRAGMA foreign_keys` סביב ה-batch | נדרש כי DROP COLUMN חייב batch recreate ב-SQLite. אומת ב-round-trip מלא (upgrade→downgrade→upgrade) מול עותק אמיתי, אבל downgrade לא רץ במסלול הרגיל בפרויקט הזה (`alembic upgrade head` בלבד לפי README) | אם אי-פעם ירוץ downgrade בפועל - לוודא שה-FK checks חוזרים דלוקים אחרי (`PRAGMA foreign_keys=ON` מופיע בסוף הפונקציה) |
| R-051-05 | סיסמה חד-פעמית מוצגת פעם אחת ב-UI, בלי ערוץ מסירה מאובטח | האדמין מעתיק/מקריא אותה ידנית לעובד. אין דוא"ל/SMS - זה מחוץ להיקף ה-patch | v0.6.0+ שיוסיף ערוץ מסירה (מייל, קישור הזמנה) יחליף את זה, לא רק יוסיף עליו |

---

# v0.5.0 — מרכז התראות

מצב: הושלם. טבלאות + מנוע כללים + endpoints (steps 1–2), ו-UI בשלושת הפורטלים
(step 3) מעל מימוש משותף אחד — `clients/shared/notifications.js`.

## (א) מקרי בדיקה

### מנוע ההתראות ו-API

| מזהה | מה בודקים | איך | תוצאה צפויה | כיסוי |
|---|---|---|---|---|
| QA-050-01 | סקופ הפיד לפי תפקיד | `GET /api/v1/notifications` כעובד / אדמין / נאמן | כל `entity_id` בתוך תחום ההרשאה בלבד | `test_employee_feed_contains_only_their_own_grants`, `test_admin_feed_is_scoped_to_their_company`, `test_trustee_feed_is_scoped_to_their_trusteeship` |
| QA-050-02 | המונה מדווח את הסך האמיתי ולא את הפיד הקטוע | `GET /notifications` + `GET /notifications/unread-count` | `count == total`. כש-`total > 50`: `items` באורך 50 בדיוק, `count` המספר המלא | `test_unread_count_matches_feed_total`, `test_feed_is_capped_but_the_count_reports_the_real_number` |
| QA-050-03 | סגירת התראה, ואידמפוטנטיות בלחיצה כפולה | `POST /notifications/{key}/dismiss` פעם ופעמיים | `204` בשתי הפעמים, שורה אחת ב-`notification_dismissals`, הפריט נעלם | `test_dismiss_removes_the_item_and_writes_exactly_one_row`, `test_dismissing_the_same_key_twice_is_idempotent` |
| QA-050-04 | העדפות ברירת מחדל = opt-out | `GET /notifications/preferences` למשתמש בלי שורות | 5 כללים, כולם `enabled=true`, `lead_days`: 14 / 30 / 30 / 7 / 90 | `test_preferences_default_to_every_rule_enabled` |
| QA-050-05 | כלל לא מוכר נדחה | `PUT` עם `"rule": "NOT_A_RULE"` | `400` · `Unknown notification rule: NOT_A_RULE` | `test_unknown_rule_is_rejected` |
| QA-050-06 | `lead_days` שלילי נדחה | אותו endpoint עם `lead_days: -1` | `400` · `lead_days must not be negative` | `test_negative_lead_days_is_rejected` |
| QA-050-07 | כיבוי כלל מוריד את הפריטים שלו מהפיד | לכבות `FULLY_VESTED_UNEXERCISED` ולרענן | הפריטים של אותו כלל נעלמים; משתמש אחר לא נפגע | `test_disabling_a_rule_removes_its_items_from_the_feed`, `test_preferences_are_per_user` |
| QA-050-08 | `VESTING_EVENT_NEAR` נדלק רק בחלון ההתרעה | מענק שמבשיל חודשית, `lead_days` צר מול רחב | צר → אין התראה; רחב → יש, עם `trigger_date` עתידי | `test_vesting_event_near_fires_only_inside_the_lead_window` |
| QA-050-09 | עובד שעזב/נפטר לא מקבל הבטחת הבשלה עתידית | פיד של עובד TERMINATED, חלון רחב | אין `VESTING_EVENT_NEAR` (ההבשלה קפואה מיום העזיבה) | `test_terminated_employee_gets_no_future_vesting_promise` |
| QA-050-10 | מענק בלי לוח הבשלה לא מפיל את הפיד | פיד של `unex@company.com` | `200`, אין התראות הבשלה על אותו מענק, `degraded_entities` ריק | ידני |
| QA-050-11 | דדליין שזז מחזיר התראה שנסגרה | לסגור התראה, לשנות את התאריך שמוליד אותה, לרענן | ההתראה חוזרת — `notification_key` מכיל את `trigger_date` בכוונה | ידני |
| QA-050-12 | ולידציה חלקית לא נשמרת | `PUT` עם שורה תקינה + שורה פסולה יחד | `400`, **והשורה התקינה לא נשמרה** | `test_an_invalid_row_rejects_the_whole_payload` |
| QA-050-13 | סגירה היא פר-משתמש | עובד סוגר התראה על מענק שהאדמין רואה גם | האדמין ממשיך לראות אותה | `test_dismissal_belongs_to_one_user_only` |

### UI — פעמון, פאנל והעדפות (שלושת הפורטלים)

מימוש אחד משותף: `clients/shared/notifications.js`. כל בדיקה כאן צריכה לעבור
**בשלושת הפורטלים** — admin (indigo), employee (emerald), trustee (purple).

| מזהה | מה בודקים | תוצאה צפויה | כיסוי |
|---|---|---|---|
| QA-050-50 | הפעמון מופיע ומציג מונה | תג אדום עם המספר; מוסתר לגמרי כשאין התראות | ידני — אומת ב-3/3 |
| QA-050-51 | פתיחת הפאנל | רשימה עם נקודת חומרה (אדום ≤7 ימים / כתום ≤30 / כחול מעבר), כותרת, פירוט ומזהה ישות | ידני |
| QA-050-52 | סגירת התראה מה-UI | הפריט נעלם והמונה יורד ב-1 בלי רענון דף | ידני — אומת (3→2) |
| QA-050-53 | תקרת הפיד גלויה למשתמש | כש-`total > 50` מופיעה שורת "מוצגות 50 מתוך N" | ידני |
| QA-050-54 | `degraded_entities` מוצג ולא מושתק | פס כתום עם מספר הישויות ומזהיהן | ידני — ראו R-050-03 |
| QA-050-55 | מסך העדפות | 5 כללים עם toggle + `lead_days`, וכל שדה עם ההסבר **של הכיוון שלו** (לפני/אחרי) | ידני — אומת |
| QA-050-56 | שמירת העדפות | toast הצלחה, המודאל נסגר, השרת מחזיר את הערך החדש, והפיד מתעדכן | ידני — אומת (2→1→2) |
| QA-050-57 | שגיאת שרת מוצגת כמו שהיא | `lead_days` שלילי → toast עם הנוסח מהשרת, המודאל **לא** נסגר | ידני |
| QA-050-58 | התנתקות עוצרת את הפולינג | אחרי logout אין קריאות `/notifications` נוספות | ידני |
| QA-050-59 | כשל בהתראות לא מפיל את המסך | ניתוק השרת → הדשבורד ממשיך לעבוד, התג נעלם | ידני |

### תיקון הבאגים המכוונים *(נכנס בגרסה הזו)*

עד v0.5.0 הבאגים האלה היו **מכוונים**. הם תוקנו באישור מפורש. הטבלה נשארת כאן כי
היא בדיקת הרגרסיה: כל שורה שחוזרת להתנהג כמו "לפני" היא רגרסיה, לא פיצ'ר.

| מזהה | הבאג המקורי | תוצאה צפויה עכשיו | כיסוי |
|---|---|---|---|
| QA-050-20 | `admin/employees` החזיר עובדים מכל החברות | `admin@comp-001.demo` מקבל 29 עובדים, כולם `COMP-001` | `test_admin_employees_returns_only_own_company` |
| QA-050-21 | `employee/dashboard/{id}` בלי בדיקת בעלות | `israel@company.com` על `EMP-BUG-OVERVEST-1` → `403 You can only view your own dashboard` | `test_employee_cannot_read_another_employees_dashboard` |
| QA-050-22 | הענקה לקטין | `POST /admin/grants` ל-`EMP-MINOR-1` → `400 ... under 18 ...` | `test_grant_to_a_minor_is_rejected` |
| QA-050-23 | הענקה בלי `birth_date` | אותו endpoint לעובד בלי תאריך לידה → `400 ... birth_date is required ...` | `test_grant_without_known_birth_date_is_rejected` |
| QA-050-24 | הבשלה נמשכה אחרי עזיבה | עובד TERMINATED — `vested` זהה בכל תאריך עתידי | `test_vesting_stops_at_termination_date` |
| QA-050-25 | החזרת אופציות לפול לא אידמפוטנטית | `PATCH /admin/employees/{id}/status` ל-TERMINATED פעמיים → הפול משתנה **פעם אחת** | ידני |
| QA-050-26 | מענק בלי לוח הבשלה הציג 0 | `unex@company.com` → `vested_options: null`, `vesting_data_missing: true`; בפורטל "נתוני הבשלה חסרים" | `test_dashboard_marks_missing_vesting_schedule_instead_of_reporting_zero` |
| QA-050-27 | אישור מעל ה-vested | `REQ-BUG-OVERVEST-1` (4000) → `400 Cannot approve 4000 options: only 1300 vested, and 0 already approved` | `test_approving_more_than_vested_is_rejected` |
| QA-050-28 | שתי בקשות חופפות אושרו יחד | `REQ-BUG-DUPLICATE-1A` → `200`; `-1B` → `400 ... only 3000 vested, and 2000 already approved` | `test_two_pending_requests_cannot_both_be_approved` |
| QA-050-29 | הגשה שחורגת מהזמין | הגשה שנייה שמצטברת מעל ה-vested → `400 ... available ...` | `test_second_overlapping_request_is_blocked_at_submission` |
| QA-050-30 | אישור לפני תום חסימת נאמן | `REQ-BUG-EARLYHOLD-1` → `400 Trustee holding period (Section 102) is not met until 2028-04-24` | `test_approving_before_trustee_holding_period_is_blocked` |
| QA-050-31 | נתיב הנאמן היה פרוץ כמו נתיב ה-admin | אותה בקשה דרך `PATCH /trustee/exercise-requests/{id}` → אותו `400` | `test_trustee_approval_path_enforces_the_same_rules` |
| QA-050-32 | קריסת 29/2 בהבשלה (500) | `bug.feb29@buglab.example` → `200`; cliff נסגר **אחורה** ל-28/2 | `test_feb29_vesting_start_no_longer_crashes_and_clamps_back` |
| QA-050-33 | קריסת 29/2 בנאמנות (`ValueError`) | הפקדה 29/2/2024 → סוף חסימה `2026-03-01` (סגירה **קדימה**) | `test_feb29_trustee_deposit_no_longer_crashes_and_rolls_forward` |
| QA-050-34 | `cliff_months // 12` | `cliff=6` → אין הבשלה עד 6 חודשים בדיוק (לא 0) | `test_cliff_not_divisible_by_twelve_is_computed_in_months` |
| QA-050-35 | הפרש חודשים קלנדרי | `start=15/1` → ב-14/2 עוד 0, ב-15/2 חודש אחד | `test_mid_month_start_does_not_vest_a_month_early` |
| QA-050-36 | `simulate-exercise` בלי בדיקת בעלות | סימולציה על מענק זר → `403` | `test_employee_cannot_simulate_exercise_on_someone_elses_grant` |
| QA-050-37 | בקשה שטופלה נסקרה שוב | `PATCH` על בקשה `APPROVED` → `409` | `test_a_reviewed_request_cannot_be_reviewed_again` |
| QA-050-38 | בקרה: דחייה עוד מותרת | דחיית בקשה חורגת → `200`, `REJECTED` | `test_rejecting_an_over_vested_request_is_still_allowed` |
| QA-050-39 | בקרה: בומרנג לא נפגע | ACTIVE עם `termination_date` היסטורי → אין PTEW, וההבשלה ממשיכה | `test_boomerang_active_employee_keeps_vesting_despite_historic_termination` |

## (ב) אזורי סיכון

| מזהה | הסיכון | למה זה סיכון | מה לבדוק |
|---|---|---|---|
| R-050-01 | ההתראות **לא נשמרות** — מחושבות בכל קריאה | אין היסטוריה: התראה שהדדליין שלה עבר נעלמת בלי עקבות, ואי אפשר להוכיח שהמשתמש הותרע | האם באמת אין תרחיש שדורש הוכחת התרעה (למשל דדליין שעבר וגרם לאובדן) |
| R-050-02 | `MAX_FEED_ITEMS = 50` | דאטה ותיק מייצר יותר מ-50 התראות; ה-UI יראה תקרה ולא עובדה | ליצור משתמש עם >50 התראות ולוודא ש-`count` מדווח נכון |
| R-050-03 | `degraded_entities` **אין לו כרגע מפיק** | הוא נבנה כדי לתפוס את קריסות 29/2, שתוקנו. הכללים כולם מגנים על `if not schedule`, ולכן הענף `except` לא נבדק בפועל | לוודא שהענף עוד עובד לפני שמסתמכים עליו |
| R-050-04 | `notification_key` מכיל `trigger_date` | סגירה "חוזרת מהמתים" בכל שינוי תאריך — התנהגות מכוונת, אבל בקלות נתפסת כבאג | לתעד למשתמש, אחרת יגיע דוח באג שגוי |
| R-050-05 | **כל תאריכי ההבשלה בדאטה הזרוע נופלים על ארבעה ימים בחודש בלבד** ({4, 15, 28, 29}) | `seed_data` בונה תאריכים ביחס ל"היום", ולכן ביום הזריעה האירוע הבא של כמעט כל מענק רחוק ~30 יום, ו-`VESTING_EVENT_NEAR` לא נדלק על אף אחד. נמדד: 0 התראות מ-251 מענקים, ומתוך 146 המענקים שהבשילו חלקית — **כולם** עם האירוע הבא ב-15–45 יום | הכלל עצמו תקין: עם `today` מוזז הוא נדלק ב-14 מתוך 31 ימים. בדיקה אמיתית של הכלל חייבת להזיז את `today` (`notif.for_employee(..., today=...)`), או לזרוע מענק עם יום-בחודש אחר |
| R-050-06 | `lead_days` נושא **שתי משמעויות הפוכות** באותה עמודה | בשלושת הכללים הראשונים זה "כמה ימים *לפני*", ובשניים האחרונים "אחרי כמה ימים". מי שמכייל בלי לקרוא יקבל את ההיפך ממה שהתכוון | ה-UI מציג הסבר נפרד לכל שדה (QA-050-55). כל כלל חדש חייב להצהיר לאיזה כיוון הוא שייך |
| R-050-07 | הפולינג הוא כל 60 שניות בכל פורטל | הפיד מחושב על קריאה, ולכן כל טאב פתוח מייצר 2 קריאות בדקה שסורקות את כל המענקים בסקופ. עם 250 מענקים זה עוד נסבל; זה לא נמדד בקנה מידה גדול | למדוד את זמן התגובה של `/notifications` על חברה עם אלפי מענקים לפני שמגדילים את התדירות |

---

# v0.4.0 — יסודות הנדסיים

## (א) מקרי בדיקה

| מזהה | מה בודקים | איך | תוצאה צפויה | כיסוי |
|---|---|---|---|---|
| QA-040-01 | הגרסה שהשרת מדווח = קובץ `VERSION` | `GET /` | שדה `version` זהה לתוכן `VERSION` | `test_root_returns_version_matching_the_version_file` |
| QA-040-02 | הבדיקות לא נוגעות ב-DB החי | `pytest` ואז לבדוק `mtime` של `esop_database.db` | לא השתנה; ה-Engine מצביע ל-DB זמני | `tests/test_harness_safety.py` |
| QA-040-03 | `alembic stamp` על DB קיים, לא `upgrade` | `alembic current` | `eca19ffceb4d (head)`. `upgrade head` על DB זרוע ייפול על `table companies already exists` | ידני |
| QA-040-04 | אין דריפט בין `models.py` לסכמה | `alembic revision --autogenerate -m probe` | גוף `upgrade()` = `pass`. **למחוק את קובץ ה-probe** | ידני |
| QA-040-05 | חיפוש סובל שגיאת הקלדה | `SearchEngine`: "Cohan" מול "Cohen" | ניקוד 0.8, העובד הנכון ראשון | `test_admin_finds_own_employee_despite_typo` |
| QA-040-06 | חיפוש לא חוצה חברות | אדמין COMP-A מחפש שם מדויק של עובדת COMP-B | אפס תוצאות Employee | `test_admin_cannot_reach_another_companys_employee_by_exact_name` |
| QA-040-07 | עובד מוצא רק את המענקים שלו | חיפוש "GRANT" כעובד | רק המענק שלו | `test_employee_search_returns_only_their_own_grants` |

## (ב) אזורי סיכון

| מזהה | הסיכון | למה | מה לבדוק |
|---|---|---|---|
| R-040-01 | `_MIN_SCORE = 0.35` מייצר false positive מאומת | שאילתה של 11 תווים עוברת את הסף מול כתובת מייל לא קשורה בניקוד 0.353 — **דיוק, לא הרשאות** | לכייל את הסף כשמישהו יתלונן על "רעש" בחיפוש |
| R-040-02 | סדר ה-import ב-`tests/conftest.py` שביר | כל import מ-`backend` שיעבור לראש הקובץ ידליף בדיקות ל-DB החי | לא לשנות את הסדר; `test_harness_safety` הוא רשת הביטחון |
| R-040-03 | `alembic.ini` חייב להישאר ASCII | Alembic קורא אותו ב-cp1252; הערה בעברית שם מפילה כל פקודה | לא להוסיף עברית לקובץ הזה |
| R-040-04 | הבדיקות בונות סכמה ב-`create_all` ולא במיגרציה | מיגרציה שבורה לא תיתפס ע"י `pytest` | QA-040-04 הוא הבדיקה היחידה שמכסה את זה — ידנית |
| R-040-05 | ה-`db_session` לא עוטף את ה-`commit` של ה-endpoint ב-savepoint | endpoint שעושה `commit` סוגר את הטרנזקציה החיצונית של הבדיקה, ואם אחר כך הוא עושה `rollback` (נתיב ה-`IntegrityError` של dismiss) ה-fixture נמחק באמצע הבדיקה. `join_transaction_mode="create_savepoint"` נראה כמו הפתרון אבל מייצר 22 שגיאות בסוויטה (הבדיקות עוברות אחת-אחת) בגלל נעילות SQLite | בדיקה שעוברת בנתיב rollback של endpoint לא תיגש ל-ORM אחריו. מי שמתקן את זה — לטפל קודם בנעילות |

---

# v0.3.0 — הבסיס

## (א) מקרי בדיקה

| מזהה | מה בודקים | תוצאה צפויה | כיסוי |
|---|---|---|---|
| QA-030-01 | יום לפני ה-cliff | 4800 אופציות, start 2022-01-01, cliff 12: ב-2022-12-31 → `0.0` | `test_vesting_at_date_boundaries` |
| QA-030-02 | יום ה-cliff עצמו | ב-2023-01-01 → `1200.0` (100/חודש × 12) | אותה בדיקה |
| QA-030-03 | אמצע הלוח | ב-2025-07-01 → `4200.0` (42 חודשים) | אותה בדיקה |
| QA-030-04 | סיום מלא ומעבר לו | ב-2026-01-01 ואחרי → `4800.0`, לא יותר | `test_vesting_never_exceeds_total_after_schedule_end` |
| QA-030-05 | הקפאה (`paused_days_total`) מזיזה את ה-cliff | 30 ימי הקפאה: ב-2023-01-15 → `0.0` במקום `1200.0` | `test_paused_days_push_the_cliff_past_the_original_cliff_date` |
| QA-030-06 | חסימת נאמן — יום לפני | הפקדה 2023-06-15: ב-2025-06-14 → `(False, 2025-06-15)` | `test_day_before_holding_period_ends_is_not_met` |
| QA-030-07 | חסימת נאמן — היום עצמו | ב-2025-06-15 → `met=True` | `test_exact_end_date_is_met` |
| QA-030-08 | מענק שלא הופקד בנאמנות | `(False, check_date)` — אין תאריך סיום אמיתי | `test_no_deposit_means_not_met_and_no_real_deadline` |
| QA-030-09 | חלון מימוש לאחר עזיבה | ראו `tests/test_post_termination_window.py` (כולל חלון 365 יום בפטירה) | אוטומטי |
| QA-030-10 | מנוע המס — שטוח ומדרגות | ראו `tests/test_tax_engine.py`, כולל בחירת גרסת טבלה לפי תאריך | אוטומטי |

## (ב) אזורי סיכון

| מזהה | הסיכון | למה |
|---|---|---|
| R-030-01 | **טבלאות המס הן נתוני דמו** | `IncomeTaxBracket` מסומן במפורש בקוד: "נתוני דמו לתרגול QA בלבד — לא חוק מס אמיתי". כל סכום מס במערכת אינו מספר אמיתי |
| R-030-02 | רק `IL_102_WORK_INCOME` מקבל מודל מדרגות מלא | `US_ISO` / `US_NSO` — בלי מגבלת $100K, בלי AMT, בלי הפרדה בין מימוש למכירה |
| R-030-03 | `currency` הוא שדה טקסט בלבד | אין המרה ואין שערי חליפין מתוארכים; מענק ב-USD ומענק ב-ILS נסכמים כאילו הם אותו דבר |
| R-030-04 | מחיר המניה נלקח מ"הרשומה האחרונה" | `simulate_exercise` שולף את `StockPricesHistory` העדכני ביותר בלי לבדוק תוקף — הערכה מלפני שנתיים תשמש חישוב של היום |
| R-030-05 | `User` — בדיוק אחד מ-3 המזהים אמור להיות מאוכלס, ולא נאכף ב-DB | משתמש עם `company_id` **וגם** `employee_id` יעבור, וההתנהגות תהיה תלוית-תפקיד |

---

## נספח א — תרחישי דאטה קבועים

**לא באגים.** מצבי עולם אמיתיים שהמערכת צריכה לדעת להציג. שווה מעבר בכל שינוי UI.

| # | תרחיש | employee_id | username | מה לבדוק |
|---|---|---|---|---|
| D-01 | נפטר לפני ה-cliff | EMP-DEC-PRECLIFF-1 | decpre1@company.com | `vested=0`, PTEW 365 יום מהפטירה |
| D-02 | נפטר אחרי ה-cliff, עדיין בנאמנות | EMP-DEC-INTRUSTEE-1 | decintrust1@company.com | חסימת נאמן ו-PTEW פעילים בו-זמנית |
| D-03 | פרש — ישראל | EMP-RETIRE-IL-1 | retireil1@harkerem.example | אין מודל פרישה; נשאר ACTIVE |
| D-04 | פרש — ארה"ב | EMP-RETIRE-US-1 | retireus1@meridian.example | US_ISO/US_NSO, בלי נאמן |
| D-05 | עזב וחזר (בומרנג) | EMP-REHIRE-1 | rehire1@boomerang.example | ACTIVE עם `termination_date` היסטורי: אין PTEW, וההבשלה ממשיכה |
| D-06 | הבשלה מלאה, לא מומש שנה | EMP-UNEXERCISED-1YEAR | unexercised_1year@patientcap.example | `FULLY_VESTED_UNEXERCISED` אמורה להופיע |
| D-07 | ללא חברה בכלל | EMP-NOCOMPANY-1 | nocompany1@orphaned.example | `company_id=NULL` — כל מסך שמניח חברה |
| D-08 | מענק ישן ללא לוח הבשלה | EMP-UNEXERCISED-1 | unex@company.com | "נתוני הבשלה חסרים", לא 0 |
| D-09 | עזב/נפטר עם מענק ללא לוח | EMP-RETIRED-1, EMP-DEC-BEFORE-1, EMP-DEC-AFTER-1 | retired@ / dec_before@ / dec_after@company.com | אותו דבר, בשילוב סטטוס |
| D-10 | 12 קטינים עם מענקים קיימים | EMP-MINOR-1..12 | minor1@company.com וכו' | דאטה היסטורי מלפני חסימת הגיל — המענקים קיימים, **הענקה חדשה** נחסמת |
| D-11 | פוטר חודש אחרי מענק | EMP-GRANT-TERM1M-1 | quickterm1@quickturn.example | יתרות הפול מול המענקים בפועל |

**ספירות בדאטה הזרוע** (נמדדו): 260 עובדים · 251 מענקים · **6 עובדים בלי
`birth_date`** · **4 מענקים בלי `VestingSchedule`** · 12 קטינים עם מענקים · 4 בקשות
מימוש PENDING במעבדת הבאגים.

## נספח ב — חשבונות כניסה

סיסמה לכולם: **`Demo1234!`**

| תפקיד | חשבון | הערה |
|---|---|---|
| אדמין חברה | `admin@comp-001.demo` | לכל חברה `admin@{company_id}.demo` |
| אדמין מעבדת באגים | `admin@comp-bugs.demo` | 4 בקשות PENDING מוכנות — כולן צריכות להיחסם עכשיו |
| נאמן | `trustee@trustee-bugs.demo` | לבדיקת נתיב אישור הנאמן |
| עובד רגיל | `israel@company.com` | EMP-001, COMP-001 |
| עובדי תרחישים | ראו נספח א | |

חשבונות מעבדה: `bug.overvest@`, `bug.duplicate@`, `bug.earlyhold@`,
`bug.feb29@buglab.example` — כולם הפכו מהדגמת באג ל**בדיקת קבלה**: כל אחד חייב
להחזיר שגיאה מנומקת, לא `200`.

## נספח ג — פערים פתוחים חוצי-גרסאות

| # | הפער | סוג |
|---|---|---|
| G-01 | האם "24 חודשים מ-29/2" מסתיים ב-28/2 או ב-1/3. המערכת בחרה **1/3** (שמרני — לא מזכה בהטבה מוקדם) | ⚠️ טעון אימות מס |
| G-02 | גיל מינימלי 18 להענקה — ברירת מחדל שמרנית | ⚠️ טעון אימות משפטי |
| G-03 | שחרור מוקדם מנאמנות ביודעין (מסלול הכנסת עבודה) חסום לגמרי | פיצ'ר עתידי, דורש אימות כלל |
| ~~G-04~~ | ~~סיסמת ברירת מחדל `Welcome123!` לכל עובד חדש~~ | **נסגר ב-v0.5.1** |
| ~~G-05~~ | ~~CORS `*` יחד עם credentials~~ | **נסגר ב-v0.5.1** |
| ~~G-06~~ | ~~Session-ים שפגו נשארים בטבלה לנצח~~ | **נסגר ב-v0.5.1** |
| G-07 | 3 תפקידים בלבד; אין רו"ח בקריאה-בלבד ואין הפרדת HR/כספים | חוב מתוכנן — v1.3.0 |
| G-08 | נעילה per-username בלבד, בלי rate limiting ברמת IP/רשת | דורש תשתית (reverse proxy/Redis) שאין בפרויקט — ראו R-051-01 |

## נספח ד — שחזור המערכת הבאגית

הבאגים המכוונים תוקנו, אבל לא אבדו:

```bash
git show qa-buggy-baseline-v1:backend/app/services/engine.py   # הקוד לפני התיקון
```

התג `qa-buggy-baseline-v1` מחזיק את הקוד וה-DB לפני התיקון, ו-
`esop_database.buggy_baseline.db` יושב בשורש. **אין להחזיר באגים לתוך קו המוצר** —
ראו `GOAL.md`.
