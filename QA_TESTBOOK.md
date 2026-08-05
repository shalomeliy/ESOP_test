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
| G-04 | סיסמת ברירת מחדל `Welcome123!` לכל עובד חדש | חוב מתוכנן — v1.0.0 |
| G-05 | CORS `*` יחד עם credentials | חוב מתוכנן — v1.0.0 |
| G-06 | Session-ים שפגו נשארים בטבלה לנצח | חוב מתוכנן — v1.0.0 |
| G-07 | 3 תפקידים בלבד; אין רו"ח בקריאה-בלבד ואין הפרדת HR/כספים | חוב מתוכנן — v1.0.0 |

## נספח ד — שחזור המערכת הבאגית

הבאגים המכוונים תוקנו, אבל לא אבדו:

```bash
git show qa-buggy-baseline-v1:backend/app/services/engine.py   # הקוד לפני התיקון
```

התג `qa-buggy-baseline-v1` מחזיק את הקוד וה-DB לפני התיקון, ו-
`esop_database.buggy_baseline.db` יושב בשורש. **אין להחזיר באגים לתוך קו המוצר** —
ראו `GOAL.md`.
