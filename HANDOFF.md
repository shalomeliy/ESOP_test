# HANDOFF — מצב העבודה הנוכחי

**מה זה:** נקודת הכניסה לכל שיחה חדשה. שיחה נסגרת → הקובץ הזה מתעדכן → השיחה הבאה
קוראת אותו ויודעת בדיוק איפה עצרנו. **הידע חי בקבצים, לא בשיחה.**

**למה זה קיים:** סשן אחד הגיע ל-79M טוקנים ו-1,031 תורים כי שלוש גרסאות נבנו בו
בלי לסגור. כל תור קורא מחדש את כל מה שנכתב לפניו, כך שהעלות גדלה עם אורך השיחה
ולא עם קושי המשימה. `CLAUDE.md` שורה 57 כבר אמרה `/clear between unrelated
exercises` — הקובץ הזה הוא מה שהופך את הכלל לבר-ביצוע: יש לאן להעביר את ההקשר.

עדכון אחרון: **2026-08-13** (v1.0.2 **שוחררה במלואה** - חמישה commits נפרדים
נדחפו ישירות ל-`main` (**לא** דרך branch/PR, בשונה מ-v1.0.0/v1.0.1 - ראו
למטה), תויגה `v1.0.2` ונדחפה, וה-DB החי עלה ל-head. שני פריטי חוב פתוחים
מ-v1.0.1 נסגרו: איחוד רישום טבלאות ייצוא/ייבוא, וחלון אישור מסמכים
פר-סוג-מסמך. ראו הבלוקים הבאים.)

> **ה-DB החי עלה ל-head ב-13/08/2026.** גיבוי לפני:
> `db_backups/esop_database.20260813-035205.db`. `alembic upgrade head`
> (`137b929afafc` → `f4b8a2d6e1c9`) - שקט, `CREATE TABLE` טהור בלי `PRAGMA
> foreign_keys=OFF/ON` (הטבלה החדשה, `document_acknowledgment_window_overrides`,
> לא FK-target של אף טבלה קיימת, אז אין `ALTER`/batch-recreate בכלל).
> אומת אחרי: `alembic_version = f4b8a2d6e1c9`, `integrity_check: ok`,
> `foreign_key_check: []`, כל הספירות זהות לפני/אחרי (25 companies,
> 260 employees, 19 pools, 251 grants, 1035 ledger_events) - ואפס דריפט.
> `document_acknowledgment_window_overrides` קיימת וריקה (כצפוי - אף UI
> עדיין לא כתב אליה לפני המיגרציה הזו).
>
> **חמשת ה-commits נדחפו ישירות לענף `main` המקומי, לא לענף פיצ'ר נפרד** -
> סטייה מודעת מהתקדים (`feat/1.0.0/database`, `feat/1.0.0/dilution-ui`,
> `feat/1.0.1/qa` - כולם עברו PR). המשתתף אושר במפורש (`AskUserQuestion`)
> להמשיך ככה בדיעבד במקום לבנות branch+PR רטרואקטיבית. `main` המקומי היה
> כבר על הענף הזה כששיחה זו התחילה (לא שונה ע"י הסוכן). תג `v1.0.2`
> (annotated, על `1bdd116`) נוצר ונדחף אחרי שתי בקשות מפורשות נפרדות
> (commit → push → תיוג → push לתג, כל אחת בתורה נפרדת).

> **v1.0.2 (13/08/2026) - סגירת חוב, שני פריטים מתוך ארבעה שהוצעו למשתתף.**
> המשתתף ביקש לתקוף את כל ארבעת פריטי החוב הפתוחים מ-`docs/qa/v1.0.1.md`
> (סיכונים 3/6) ו-`docs/qa/v1.0.0.md` (סיכונים 3/8) לפני v1.1.0. תהליך
> התכנון המלא לפי `CLAUDE.md` שלב 4: שלושה `Explore` agents מיפו את הקוד
> הקיים, אחריהם חמישה מומחי-סקירה read-only (product/architecture/design/
> qa-expert + security-engineer) לפני תכנון קונקרטי. **product-expert חלק
> במפורש** על שני מהפריטים (חיבור מימוש↔טבלת ההון, `ShareIssuance` סוג-
> אירוע יחיד) - אין צורך עסקי מאומת היום, וההיקף האמיתי דורש סוג אירוע
> ledger חדש + עמודת מעקב חדשה + שינוי בנוסחת ה-fully-diluted + החלטה על
> נתונים היסטוריים. הוצג למשתתף במפורש (`AskUserQuestion`, לא הוחלט מראש),
> **ושני הפריטים האלה נדחו במפורש** - נשארים פתוחים, בדיוק כמו קודם, לא
> חוב שנצבר בשקט (ראו "הצעד הבא" למטה). תוכנית טכנית קונקרטית לשני
> הפריטים שנותרו נכתבה ואושרה (`ExitPlanMode`) ב-
> `C:\Users\Shalom\.claude\plans\sunny-toasting-willow.md`.
>
> **פריט 1 - איחוד `export.py::TABLE_REGISTRY` ו-`import_.py`'s
> `_TABLE_SPECS`/`_PK_COLUMN`/`_MODEL_BY_TABLE`/`_FORCE_COMPANY_ID_TABLES`/
> `_NULLED_USER_COLUMNS`/`_AGGREGATE_TYPE_BY_TABLE`** (`docs/qa/v1.0.1.md`
> סיכון 3, **נסגר**). מודול חדש `backend/app/services/company_scope.py` -
> `TableSpec`/`TABLE_REGISTRY` אחד (11 טבלאות ליבה, אותו סדר טופולוגי
> שכבר נדרש קודם), `CompanyScope`/`build_company_scope` עברו אליו (היו
> כבר משותפים דה-פקטו - `import_.py` ייבא אותם מ-`export.py`).
> `_LEDGER_AGGREGATE_CATEGORY` (`import_.py`) נגזר עכשיו מה-registry
> המאוחד במקום דיקט נפרד שהיה חייב להישאר מסונכרן ידנית - אותה צורת-בעיה
> בדיוק. `SPECIAL_CASED_TABLES` מתעד במפורש ארבע טבלאות עם עמודת
> `company_id` בפועל שנשארות מחוץ להיקף הייצוא/ייבוא כליל
> (`companies`/`users`/`ledger_ownership`/`stock_prices_history`) - היו כך
> גם קודם (החלטות מפורשות משכבר), רק לא היו מתועדות במקום אחד.
> **התיקון האמיתי הוא אינווריאנט קבוע חדש, לא הריפקטור עצמו** (סיכום
> product/qa/security-engineer בתכנון): `tests/test_project_invariants.py::
> test_every_company_scoped_table_is_registered_or_explicitly_special_cased`
> - כל מודל עם עמודת `company_id` בפועל (נבדק דרך `Base.metadata`, לא
> ניחוש) חייב להופיע ב-`TABLE_REGISTRY` (ומצהיר `force_company_id=True`)
> או ב-`SPECIAL_CASED_TABLES` המתועד - אין דרך שלישית להישאר בשקט מחוץ
> לשניהם. **אומת ידנית שאינו ריק**: הוסרה זמנית `force_company_id=True`
> משורת `shareholders`, הבדיקה נכשלה בפועל בדיוק על השורה הזו, הוחזר
> לתקין מיד אחרי.
>
> **פריט 2 - חלון אישור מסמכים פר-סוג-מסמך** (`docs/qa/v1.0.1.md` סיכון 6,
> **נסגר בחלקו במפורש** - פר-(חברה,סוג-מסמך), לא גרנולריות עדינה יותר,
> כפי שהוחלט ב-v1.0.1 עצמה ולא הורחב כאן). טבלה חדשה
> `document_acknowledgment_window_overrides` (`UNIQUE(company_id,
> template_type)`, `CHECK(window_days > 0)`, מיגרציה `f4b8a2d6e1c9` -
> `CREATE TABLE` טהור, בלי `PRAGMA foreign_keys`/batch-recreate כי אין
> `ALTER` על טבלה קיימת - נמוך-סיכון יותר מ-`137b929afafc` שהוא מרחיב).
> שלושה מקורות בסדר עדיפות (`resolve_acknowledgment_window_days`,
> `document_status.py`): override פר-(חברה,סוג) → override פר-חברה
> (v1.0.1, `Company.acknowledgment_window_days`) → הקבוע הגלובלי
> (`ACKNOWLEDGMENT_WINDOW_DAYS`). `deadline_for()` עצמה נשארה טהורה בלי
> `db` - `documents.py::_transition_document` פותר את ה-override ומעביר,
> אותה מוסכמה בדיוק כמו קודם. `GET`/`PUT /admin/company/acknowledgment-
> windows[/{template_type}]` חדשים ב-`company.py`, `400` נקי לפני ה-`CHECK`
> (אותו לקח בדיוק כמו `total_authorized_shares`/`acknowledgment_window_days`
> הכלליים). UI - טבלה קטנה בטאב "פרופיל חברה" (`index_manage.html`), ערך
> "בפועל" מנוסח תמיד במפורש ("משתמש בברירת המחדל של החברה (X ימים)"), לא
> תא ריק - אותה מוסכמה כמו "לא זמין" בטבלת ההון. **אומת ידנית מקצה לקצה
> מול סנדבוקס חי** (הגדרת חריגה, מחיקתה/חזרה לירושה, דחיית ערך שלילי -
> toast נקי, אפס שגיאות קונסולה). **must-have test אומת שאינו ריק**:
> `_transition_document` הוחזר זמנית לשאילתה הישנה (בלי הפותר החדש),
> הבדיקה נכשלה בפועל, הוחזר לתקין.
>
> **בדיקות:** 8 פונקציות בדיקה חדשות (חלקן פרמטריות) ב-`tests/test_documents.py`
> (positivity, fallback-chain תלת-שכבתי, בידוד חוצה-חברות, מחיקה/חזרה
> לירושה, סוג מסמך לא-נתמך, אי-רטרואקטיביות) + בדיקת אינווריאנט אחת חדשה
> ב-`tests/test_project_invariants.py`. הסוויטה המלאה: **420 עוברות,
> 0 נכשלות** (הייתה 412 בתחילת השיחה). `docs/qa/v1.0.2.md` חדש -
> `QA-102-01` עד `QA-102-14`, סוגר סיכונים 3+6 של v1.0.1 בטבלת הסיכונים,
> ומתעד סיכון חדש (ארבע הטבלאות עם `company_id` מחוץ להיקף הייצוא/ייבוא -
> היו כך גם קודם, עכשיו מתועד ונבדק לראשונה). `QA_TESTBOOK.md` עודכן,
> v1.0.2 סומן פעיל, v1.0.1 עבר לארכיון.
>
> **סקירה עצמאית (change-reviewer, 13/08/2026): `REPAIR` → תוקן.** שני
> ממצאים: (1) **חוסם** - `HANDOFF.md` (הקובץ הזה) לא עודכן בזמן - תוקן
> עכשיו כחלק מסגירת הממצא הזה, בדיוק לפי `CLAUDE.md` ("שיחה לא נסגרת בלי
> לעדכן HANDOFF.md... זו הכשל היחיד שמצטבר בשקט חוצה-גרסאות"). (2) לא-חוסם
> - ה-`PUT /admin/company/acknowledgment-windows/{type}` endpoint החזיר
> אובייקט ORM גולמי בענפי create/update בלי `response_model` מוצהר (דלף
> `override_id`/`company_id` - שדות פנימיים, לא עקבי עם שכניו באותו קובץ
> שכולם מצהירים `response_model`) - תוקן: `DocumentAcknowledgmentWindowOverrideOut.window_days`
> הפך ל-`Optional[int]` (כדי לשרת גם את ענף המחיקה תחת אותו schema),
> ו-`response_model` נוסף ל-endpoint. הסוויטה המלאה אחרי שני התיקונים:
> 420, ללא שינוי. **הסקירה אימתה בעצמה, לא רק סמכה על הדיווח**: הריצה את
> `pytest` המלא פעמיים, שחזרה בעצמה את בדיקת אי-הריקות של האינווריאנט
> החדש (הסרה/שחזור עצמאיים של `force_company_id`, לא רק קריאת התיאור), ואת
> רשימת 12 הטבלאות עם עמודת `company_id` ישירות מול `Base.metadata` -
> תואמת בדיוק את החלוקה בין `TABLE_REGISTRY` (7) ל-`SPECIAL_CASED_TABLES` (4)
> (חמש עשרה מתוכן כלל לא צריכות ולידציה - grants/vesting_schedules/
> exercise_requests/exercise_tax_records אין להן עמודת company_id בכלל).
>
> **`VERSION` → `1.0.2`.** **טרם בוצע commit** - כל השינויים (11 קבצים
> משתנים + 2 קבצים חדשים: `company_scope.py`, המיגרציה) עדיין ב-working
> tree בלבד. commit/push/merge/תיוג ממתינים לבקשה מפורשת נפרדת של
> המשתתף בתורה נפרדת, לפי אותו פרוטוקול בדיוק כמו כל גרסה קודמת - שום
> פעולת git לא בוצעה כאן ביוזמה עצמאית.

> **ה-DB החי עלה ל-head ב-12/08/2026, אחרי המיזוג.** גיבוי לפני:
> `db_backups/esop_database.20260812-162625.db`. `alembic upgrade head`
> (`bd65db40f654` → `137b929afafc`) - שקט, בלי `PRAGMA foreign_keys=OFF/ON`
> (בשונה מהמיגרציה הקודמת: `ADD COLUMN`+`CHECK` על `companies` בלי FK חדש
> נכנס/יוצא, אז אין `create_foreign_key` שמחייב recreate-עם-DROP על טבלה
> שמצביעים אליה). אומת אחרי: `alembic_version = 137b929afafc`,
> `integrity_check: ok`, `foreign_key_check: []`, כל הספירות זהות לתיעוד
> הקודם (251 grants, 19 pools, 260 employees, 1035 ledger_events, 25
> companies) - ואפס דריפט. `companies.acknowledgment_window_days` קיימת
> ו-`NULL` בכל 25 השורות (אין backfill, כמתוכנן).
>
> **v1.0.1 (12/08/2026) - patch תיקוני-באגים, ארבעה פריטים שנבחרו מרשימת
> החוב הפתוחה של v1.0.0/v0.9.1.** תהליך התכנון המלא לפי `CLAUDE.md` שלב 4:
> שלושה `Explore` agents מיפו את הקוד הקיים, אחריהם חמישה מומחי-סקירה
> read-only (product/architecture/design/qa-expert + security-engineer, כי
> שני פריטים נוגעים ב-PII/scoping חוצה-חברות) לפני תכנון קונקרטי. שאלה
> מוצרית אחת גנוזה חזרה למשתתף ואושרה (ראו למטה, פריט 3). תוכנית טכנית
> קונקרטית נכתבה ואושרה ב-`C:\Users\Shalom\.claude\plans\glistening-skipping-melody.md`.
>
> **פריט 1 - ייצוא/ייבוא לא כלל את טבלת ההון** (`docs/qa/v1.0.0.md` סיכון 4,
> **נסגר**). `ShareClass`/`Shareholder`/`ShareIssuance` נרשמו ב-
> `services/export.py`'s `TABLE_REGISTRY`+`CompanyScope`, ובמבני
> `services/import_.py` המקבילים (`_TABLE_SPECS`/`_MODEL_BY_TABLE`/
> `_PK_COLUMN`/`_FORCE_COMPANY_ID_TABLES`/`_LEDGER_AGGREGATE_CATEGORY`/
> `_AGGREGATE_TYPE_BY_TABLE`/`_record_ownership_for_new_row`).
> `OptionPool.share_class_id` קיבל `fk_check` נאלבילי. **ממצא אבטחה קריטי
> שהתכנון עצמו חשף מראש ואומת בבדיקה**: `_FORCE_COMPANY_ID_TABLES` חייב
> לכלול את שלוש הטבלאות החדשות - בלעדיו, bundle מזויף/מיושן עם `company_id`
> זר על שורת `shareholders`/`share_issuances` היה נכתב עם הערך מהקובץ,
> לא נדרס לחברת היעד (P2, `QA_TESTBOOK.md`). נעול ב-
> `test_commit_force_overwrites_a_crafted_foreign_company_id_on_shareholders_and_share_issuances`.
> **באג נוסף נמצא תוך כדי כתיבת בדיקות הרגרסיה** (לא אחד מארבעת הפריטים
> המתוכננים): `_TABLE_SPECS`/`_MODEL_BY_TABLE` עיבדו `option_pools` לפני
> `share_classes`, כך שהגירת חברה מלאה עם סוג מניה חדש *בתוך אותו batch*
> נכשלה ב-`ERROR` שגוי. תוקן בהזזת `share_classes` לפני `option_pools`
> בשני המבנים, ולא הושאר כ-`xfail` מתועד.
>
> **פריט 2 - `holding_period_end_date` הציג "היום" במקום "לא ידוע"**
> (**נסגר**). `check_trustee_holding_period` מחזיר `(False, today)` בכוונה
> כש-`trustee_deposit_date` חסר (חוזה נכון של המנוע) - אבל ארבע נקודות
> קריאה (`trustee.py`, `employee_dashboard.py`, `exercise_requests.py`'s
> `simulate_exercise`+`_assert_request_approvable`) הציגו/ניסחו את זה כמו
> תאריך אמיתי. עוזר משותף חדש, `_trustee_holding_status`, לצד `_vested_at`
> ב-`exercise_requests.py` (אותו דפוס ייבוא חוצה-מודולים). `ExerciseSimulationResponse.holding_period_end_date`
> הפך ל-`Optional[date]`. **תוקן גם ב-UI**: `clients/employee_portal/index_emp.html`
> היו לו שני מקומות לא-מוגנים שהיו מרנדרים `"בחסימה עד null"` ומספר ימים
> שלילי-אבסורדי מ-epoch - `clients/trustee_portal` כבר היה מוגן נכון, אותו
> דפוס הועתק.
>
> **פריט 3 - אין ולידציה על `termination_date`** (**נסגר**). עוזר טהור חדש,
> `_validate_termination_date`, מוחל **זהה** על `DELETE /admin/employees/{id}`
> ו-`PATCH .../status` - כדי לא לשחזר P3 (`QA_TESTBOOK.md`: ולידציה בנתיב
> אחד וחסרה בשני). **החלטה מוצרית אחת נגזרה בחזרה למשתתף ואושרה** (מבין
> שלוש אופציות שהוצגו): לחסום תאריך עתידי (`> business_today()`), לא רק
> תאריך שלפני הגיוס - כדי לתפוס גם טעויות הקלדה של שנה שגויה, לא רק
> backdating. בדיקה פרמטרית אחת חוצה את שני הנתיבים בבת אחת.
>
> **פריט 4 - חלון אישור מסמכים היה קבוע גלובלי יחיד** (`docs/qa/v0.9.1.md`
> סיכון 10, **נסגר בחלקו** - פר-חברה, לא פר-סוג-מסמך, כפי שהוחלט מפורשות
> בתכנון). `Company.acknowledgment_window_days` (נאלבילי, `CHECK > 0`,
> מיגרציה `137b929afafc` - אומתה upgrade→downgrade→upgrade על sandbox
> נפרד). `deadline_for()` נשאר פונקציה טהורה בלי `db` (הקורא פותר את
> ה-override ומעביר), בדיוק כדי לא לפתוח מחדש את הבחנת timestamp-מול-קלנדרי
> שח1/ח2 (HANDOFF.md) תיקנו. `PUT /admin/company` דוחה `<= 0` ב-`400` נקי
> *לפני* ה-`CHECK` - אותו לקח בדיוק כמו `total_authorized_shares==0.0`
> (v1.0.0 שלב ב). UI חדש ב-`clients/admin_portal/index_manage.html`, אותו
> דפוס בדיוק כמו השדה ההוא. **אין השפעה רטרואקטיבית** על מסמכים שנשלחו
> לפני שינוי ה-override - נבדק במפורש.
>
> **בדיקות:** 19 פונקציות בדיקה חדשות (חלקן פרמטריות) על פני חמישה קבצים
> קיימים (`tests/test_export.py`, `test_import_commit.py`,
> `test_termination_date_is_explicit.py`, `test_trustee_holding.py`,
> `test_documents.py`). הסוויטה המלאה: **411 עוברות, 0 נכשלות** (הייתה
> 381 בתחילת השיחה). `docs/qa/v1.0.1.md` חדש - `QA-101-01` עד `QA-101-26`,
> טבלת סיכונים סוגרת את סיכון 4 של v1.0.0 ומתעדת שני סיכונים חדשים (סדר
> עיבוד ב-import_.py שנמצא ותוקן; שני מבנים מקבילים ב-export/import שעדיין
> לא אוחדו - נדחה בכוונה, לא חוב שנצבר בשקט). `QA_TESTBOOK.md` עודכן,
> v1.0.1 סומן פעיל, v1.0.0 עבר לארכיון.
>
> **סקירה עצמאית (change-reviewer, 12/08/2026): `PASS`, אפס חוסמים** על
> כל ארבעת הפריטים + תיקון הסדר. אימת בעצמו: `pytest` מלא (411/0, זהה),
> `alembic upgrade head` נקי על sandbox נפרד, וכיסוי מבחני-רגרסיה אמיתי
> (לא רק "מחזיר 200") - כולל בדיקת `_FORCE_COMPANY_ID_TABLES` הקריטית
> לאבטחה. ממצא לא-חוסם יחיד: אין בדיקת cross-company מפורשת ל-
> `share_issuances`'s שני ה-FK (רק "לא קיים בכלל") - מנגנון ה-fk_check
> כבר מוכח על טבלאות אחרות, לא נדרש תיקון.
>
> **`VERSION` → `1.0.1`.** שבעה commits נפרדים על ענף `feat/1.0.1/qa` (שנוצר
> ע"י אחד מהסוכנים - אותה מוסכמת-ענף-לכל-סוכן כמו `feat/1.0.0/database`),
> אחד לכל דאגה (`fix(export-import)`, `fix(exercise)`, `feat(employees)`,
> `feat(documents)`, `docs(qa)`, `chore(release)` ל-`VERSION`, `handoff`).
> **כל פעולה בוצעה רק אחרי בקשה מפורשת נפרדת של המשתתף בתורה נפרדת** - commit,
> push, פתיחת PR (`gh pr create` → PR #5), מיזוג (`gh pr merge 5 --merge` -
> merge commit, לא squash/rebase, אותו דפוס כמו PR #3/#4), ותיוג
> (`v1.0.1` annotated, על ה-merge commit `520b16a`, נדחף). `main` מקומי
> סונכרן (`git checkout main && git pull --ff-only`) מיד אחרי המיזוג.
> `feat/1.0.1/qa` נשאר קיים ב-remote (לא נמחק, אותו תקדים).

> **שלב ב בפועל (המשך ישיר לאותה שיחה שסגרה את שלב א ומיזגה אותו ל-`main`):**
> `git checkout main && git pull` בוצע ראשון (12 commits, fast-forward טהור) -
> ראו הערת שלב א למטה. סקירה ב-5 מומחים read-only (product/architecture/
> design/qa-expert + security-engineer, כי הפיצ'ר נוגע באחוזי-בעלות וב-PII
> של בעלי מניות) לפני תכנון קונקרטי, בדיוק לפי `CLAUDE.md` שלב 4. שתי הכרעות
> חזרו למשתתף ואושרו (שתיהן ההמלצה המסונתזת, לא רק אופציה אחת שהוצגה):
> 1) **fully-diluted = `outstanding_shares` (סכום `ShareIssuance.shares`) +
> `total_shares` של כל פול אופציות, פעם אחת per פול** - לא רק מוקצה, לא רק
> ממומש, המילואה כולה. 2) **אישור `ExerciseRequest` לא יוצר `ShareIssuance`/
> `Shareholder`** - נשאר בדיוק כמו שהיה, בכוונה: זה מה שמונע ספירה כפולה
> כשמחברים את שני הצדדים (החלטה 1 תלויה בהחלטה 2). תוכנית טכנית קונקרטית
> נכתבה ואושרה במפורש (`ExitPlanMode`) ב-
> `C:\Users\Shalom\.claude\plans\floating-moseying-hellman.md`.
>
> **מומש:** `backend/app/services/cap_table.py::compute_cap_table_snapshot`
> (אגרגציה טהורה בזמן קריאה, בלי persist, בלי ledger event type חדש) +
> `GET /admin/cap-table/snapshot?as_of=` חדש ב-`cap_table.py` + 3 schemas
> חדשים. `ShareIssuance` מסונן בעמודה ישירה (שקול ל-replay רק כי יש לו
> סוג אירוע יחיד - ראו סיכון 3 ב-`docs/qa/v1.0.0.md`); `OptionPool` עובר
> `project(as_of_effective_date=...)` הקיים לתאריך היסטורי, ועמודה מוטטת
> ישירה ל"עכשיו"/עתיד. פול בלי היסטוריית ledger כלל מוחרג במפורש
> (`partial=True` + `warnings`), לא נספר כ-0 ולא מקריס. **UI**: שתי לשוניות
> חדשות ב-`clients/admin_portal/index_manage.html` - "ניהול פולים ומניות"
> (חושף לראשונה UI לשלושת ה-endpoints שקיימים משלב א ולא נחשפו) ו-"טבלת
> הון" (בעלי-מניות, הנפקות, ופאנל סיכום עם `as_of` + banner "לא זמין" -
> לעולם לא `0%` שקרי) - ותוספת שדה `total_authorized_shares` לטאב company
> (השדה קיים מ-שלב א, אף UI לא חשף אותו עד עכשיו).
>
> **סקירה עצמאית (change-reviewer) תפסה חוסם אמיתי לפני סגירה: `REPAIR`.**
> `total_authorized_shares == 0.0` - ערך ממשי, לא `None`, בלי ולידציית
> positivity ב-`CompanyUpdateRequest` - עבר את בדיקת `is not None` בקוד
> המקורי והפיל `ZeroDivisionError` לא-מטופל (`500` גולמי). **נחשף עצמאית
> גם על ידי, לפני שהסקירה חזרה** (נבדק ידנית מול sandbox חי: קריסה אמיתית,
> לא תיאורטית - אדמין שמקליד `0` בשדה שהתוכנית הזו עצמה חשפה לראשונה
> ב-UI). תוקן: `total_authorized_shares > 0` בנוסף ל-`is not None` -
> מבחינת דילול, `0` אינו מכנה תקין, בדיוק כמו `None` (שני האחוזים `None`,
> לא קורס, לא `0%` שקרי). בדיקת רגרסיה נוספה (`QA-100-49`) ואומתה שאינה
> ריקה (נכשלת בפועל מול הגרסה הלא-מתוקנת, נבדק ידנית). **אחרי התיקון:
> `PASS`** על כל שאר הממצאים (נוסחת הדילול, `None`-handling, פול-חלקי,
> scoping חוצה-חברות, תאריך עתידי, RBAC, דיוק מסמכי ה-QA).
>
> **בדיקות:** `tests/test_cap_table_snapshot.py` חדש - 8 בדיקות (דוגמה
> מחושבת-ביד, `None`, פול "unassigned", פול-יתום היסטורי, scoping
> חוצה-חברות, `as_of` עתידי, 403, ותקרה=0 - האחרונה נוספה אחרי הסקירה).
> הסוויטה המלאה: **381 עוברות, 0 נכשלות** (הייתה 373 בתחילת השיחה).
> `docs/qa/v1.0.0.md`: `QA-100-42` עד `QA-100-49`, סיכונים 1-2 סגורים,
> סיכון 3 קיבל תלות מתועדת חדשה, סיכון 7 עבר מ"מתוכנן" ל"נבדק", סיכון 8
> חדש (ניתוק מימוש↔`ShareIssuance` - דחייה מפורשת ומכוונת של המשתתף, לא
> חוב שנצבר בשקט). **אומת ידנית מקצה לקצה מול sandbox חי** (login, יצירת
> סוג מניה/פול/בעל-מניות/הנפקה, מספרים תואמים חישוב ביד, banner "לא זמין"
> על תקרה=0, שמירת השדה בטאב company) - לא רק סוויטת ה-pytest.
>
> **`feat/1.0.0/dilution-ui`** (ענף חדש, מ-`main` מעודכן) - 5 commits
> נפרדים, **נדחפו ל-origin, ואז PR #4 נפתח ומוזג ל-`main`** (`gh pr create`
> → `gh pr merge 4 --merge`, אחרי אישור מפורש נפרד לכל אחת משתי הפעולות -
> merge commit, לא squash/rebase, אותו דפוס בדיוק כמו PR #3 של שלב א).
> `main` מקומי סונכרן (`git checkout main && git pull --ff-only`) מיד
> אחרי המיזוג. **תג `v1.0.0` נוצר** (annotated, על ה-merge commit
> `9ef5d65`) **ונדחף** - אחרי אישור מפורש שלישי ונפרד. `VERSION` לא זז
> (נשאר `1.0.0`, נקבע בסוף שלב א) - שלוש הפעולות (merge/תיוג/push) בוצעו
> כל אחת רק אחרי בקשה מפורשת של המשתתף בתורה נפרדת, לא כחבילה אחת.

> **זו סגירת גבול גרסה (v1.0.0 שלב א + שלב ב), לא סגירת מיזוג.** v1.0.0
> נפתחה בשני שלבים (ראו `C:\Users\Shalom\.claude\plans\parallel-jingling-bear.md`
> לשלב א ו-`floating-moseying-hellman.md` לשלב ב): **שלב א** — מודל דאטה +
> ledger + CRUD/list API לטבלת ההון (`ShareClass`, `Shareholder`,
> `ShareIssuance`) + תמיכה במספר פולים לחברה. **שלב ב** — חישוב דילול/
> fully-diluted + מסכי UI — **הושלם (ראו למעלה).** שני השלבים סגורים,
> בדיוק כמו התקדים ב-v0.9.1 (VERSION זז בסוף שלב א, התג ימתין למיזוג
> הענף הזה ל-`main`).
>
> **שלב א בפועל:** שלוש טבלאות חדשות (`ShareClass`/`Shareholder`/
> `ShareIssuance` — האחרונה ledger-native מהיום הראשון, לא פרויקציה
> שנוספה בדיעבד, כדי ש-snapshot היסטורי בשלב ב יהיה אפשרי מבנית), שתי
> עמודות nullable (`Company.total_authorized_shares`,
> `OptionPool.share_class_id`), מיגרציה אחת אדיטיבית (`bd65db40f654`,
> upgrade/downgrade אומתו ידנית במחזור מלא על sandbox), ראוטר חדש
> `backend/app/api/cap_table.py`, ו-`POST /admin/pools` חדש (עד כה רק
> `seed_data.py` יצר פול). **שני באגים אמיתיים נמצאו ותוקנו בסקירת ה-QA
> של השלב הזה** ב-`create_shareholder`: `employee_id` חוצה-חברה עבר בלי
> 403 (IDOR), ו-`employee_id` שלא קיים קרס ב-`IntegrityError` לא מטופל
> (500 במקום 404) — שניהם נעולים כעת בבדיקת רגרסיה (`tests/test_cap_table.py`).
> **סקירה עצמאית (change-reviewer): `PASS`, אפס חוסמים.** הסוויטה המלאה:
> **372 עוברות, 0 נכשלות** (הבדיקה שחסמה עד עכשיו,
> `test_version_file_is_not_behind_the_qa_testbook`, עברה אחרי bump
> ה-`VERSION`). 6 commits נפרדים על `feat/1.0.0/database`, **לא נדחפו**.

> **ה-DB החי עלה ל-head ב-11/08/2026 (סבב שני, אחרי בקשה מפורשת של המשתתף).**
> נכון ל-11/08/2026 בבוקר הוא היה בסטייה — `b7c4d1e9f2a3`, שלוש מיגרציות
> מאחורי `bd65db40f654` (ראו ההערה הבאה למטה לפירוט המקורי). המשתתף אישר
> במפורש להריץ `alembic upgrade head` עם גיבוי קודם, כמו ב-09/08.
>
> **הריצה הראשונה נכשלה — וחשפה באג אמיתי במיגרציה `bd65db40f654`, לא
> תקלת סביבה.** `option_pools` הוא FK target קיים מ-`grants.pool_id`
> (251 שורות אמיתיות ב-DB החי). `create_foreign_key` על `option_pools`
> (הוספת ה-FK ל-`share_classes`) מחייב SQLite לבצע recreate מלא של הטבלה
> ב-batch mode (טבלה חדשה + copy + **DROP הישנה** + rename) — וה-DROP נכשל
> ב-`FOREIGN KEY constraint failed` כל עוד `grants` מפנה אליה. המיגרציה
> נעצרה אחרי שיצרה בהצלחה `share_classes`/`shareholders`/`share_issuances`
> אך לפני שסיימה לגעת ב-`option_pools`, והשאירה טבלת עזר תקועה
> (`_alembic_tmp_option_pools`) — **לא** קרס ה-DB (`integrity_check: ok`,
> `alembic_version` נשאר על הגרסה הקודמת), אבל היה במצב לא-עקבי. **שוחזר
> מהגיבוי** (`db_backups/esop_database.20260811-114806.db`, שנלקח מיד לפני
> הריצה) לפני שנעשה עוד דבר.
>
> **הבדיקה המקורית לא תפסה את זה כי היא רצה מול סכימה ריקה** — גם סוויטת
> ה-pytest וגם אימות ה-sandbox של שלב א (upgrade→downgrade→upgrade,
> מתועד למטה) בדקו את המיגרציה על DB בלי שורות `grants` אמיתיות. הבאג
> מתגלה רק כששורה קיימת מפנה בפועל ל-`option_pools`.
>
> **תוקן**: `PRAGMA foreign_keys=OFF/ON` סביב בלוק ה-`batch_alter_table`
> על `option_pools`, גם ב-`upgrade()` וגם ב-`downgrade()` (אותו דפוס
> בדיוק כמו 56baedac6e53/`national_id` — תועד בהערת קוד במיגרציה עצמה).
> **אומת בשלושה שלבים לפני שהופעל שוב על ה-DB החי**: (1) עותק של הגיבוי
> האמיתי (251 grants/19 pools) עבר `upgrade head`→`downgrade -1`→
> `upgrade head` בלי שגיאה, `foreign_key_check` ריק בכל שלב; (2) נוספה
> בדיקת רגרסיה (`tests/test_migrations.py`) שמזריעה שורת `grants` שמפנה
> ל-`option_pools` לפני ההעלאה — **אומתה שאינה ריקה**: נכשלת בפועל מול
> הגרסה הלא-מתוקנת של המיגרציה (הוחזרה זמנית ב-`git stash`), עוברת עם
> התיקון; (3) הסוויטה המלאה: **373 עוברות** (הייתה 372, +1 הבדיקה החדשה).
> `esop_database.db` עלה ל-head בהצלחה בריצה השנייה — `alembic_version =
> bd65db40f654`, `integrity_check: ok`, `foreign_key_check: []`, כל
> הנתונים הקיימים ללא שינוי (251 grants, 19 pools, 260 employees, 1035
> ledger_events). שלוש הטבלאות החדשות קיימות וריקות (`share_classes`/
> `shareholders`/`share_issuances` — 0 שורות, כצפוי — שום UI/ייבוא לא
> כתב אליהן עדיין). שלושה commits נפרדים על `feat/1.0.0/database`:
> תיקון המיגרציה, בדיקת הרגרסיה, ותיעוד ב-`docs/qa/v1.0.0.md` (`QA-100-41`).
>
> **גם ה-6 commits הקודמים של שלב א נדחפו** (`git push -u origin
> feat/1.0.0/database`) — לפני כן היו רק מקומיים.

> **ה-DB החי הועלה ל-head ב-09/08/2026 (הערה מקורית, לא לפתוח מחדש).** הוא
> היה שלוש מיגרציות מאחור ובלי טבלת `documents` — כלומר כל v0.9.0 לא היה
> קיים בו. גיבוי לפני: `db_backups/esop_database.20260809-030922.db`
> (מחוץ ל-git, מכיל נתונים אמיתיים). אחרי: תקין, 4 טריגרים, ספירות זהות,
> אפס דריפט מול המודלים. **הדריפט המחודש המתועד בהערה שלמעלה הוא אירוע
> חדש, נפרד** — הצטבר מאז דרך שתי גרסאות שהמיגרציה שלהן לא הוחלה על ה-DB
> החי.

---

## איפה אנחנו

| | |
|---|---|
| גרסה | **v1.0.2 — שוחררה במלואה.** נדחפה ל-`main` (ישירות, לא PR), מתויגת `v1.0.2` |
| תוכנית טכנית | `sunny-toasting-willow.md` |
| `VERSION` | `1.0.2` — תג `v1.0.2` **קיים**, annotated, נדחף ל-`origin` |
| בדיקות | `420 passed, 0 failed` (הייתה `412` בתחילת השיחה) |
| git | `main` על `1bdd116` (חמישה commits נפרדים, נדחפו ישירות - ראו הבלוק בראש הקובץ) |
| מיגרציה | `f4b8a2d6e1c9` (`document_acknowledgment_window_overrides`), על גבי `137b929afafc`. **הוחלה על ה-DB החי** ב-13/08/2026 (ראו הבלוק בראש הקובץ) |
| DB חי | **עלה ל-head.** `esop_database.db` על `f4b8a2d6e1c9`, `integrity_check: ok`, אפס דריפט |

## הצעד הבא

**v1.0.2 סגורה, נדחפה, מתויגת, וה-DB החי עלה ל-head - אין משימה פתוחה
שממתינה להמשך מיידי.** השיחה הבאה פותחת גרסה חדשה (הבאה בתור לפי
`FEATURE_SPEC.md`: **v1.1.0 - דוחות, ייצוא ו-BI**) או חוזרת לאחד משני הפריטים
שנדחו למטה, לפי בחירת המשתתף - לא ממשיכה את v1.0.2.

> **`FEATURE_SPEC.md` שונתה - סדר 1.1.0–1.6.0 הוזז (13/08/2026), בשיחה
> נפרדת מזו שסגרה את v1.0.2.** בקשת המשתתף: להעדיף בסבב הקרוב פיצ'רים
> גלויים-ללקוח על פני תשתית וחישובי-מס מיוחדים. **תוכן כל גרסה לא השתנה -
> רק הסדר/המספור.** מיפוי ישן→חדש (פירוט מלא ונימוק פר-גרסה בהערה בראש
> `FEATURE_SPEC.md`): הערכות שווי 1.1.0→**1.4.0** · דוחות/BI 1.2.0→**1.1.0**
> · קשיחות/RBAC 1.3.0→**1.5.0** · אירועי חברה/סימולטור אקזיט 1.4.0→**1.2.0**
> · מיסוי רב-מדינתי 1.5.0→**1.6.0** · אינטגרציות 1.6.0→**1.3.0**. **החלטה
> נגזרת מפורשת באותה שיחה:** חוב האבטחה החי (CORS `*`+credentials, סיסמת
> ברירת מחדל קבועה `Welcome123!`, session-ים שלא נמחקים - הערה 2א
> ב-`FEATURE_SPEC.md`) **לא** יחולץ כ-patch מוקדם כפי שהמסמך הציע - נשאר
> מתועד בקבוצה של v1.5.0 (החדשה), עד סוף התור. הנימוק: שום גרסה עדיין לא
> יצאה בפועל ללקוח (טסטבד/תרגול בלבד), ולכן אין דחיפות לחלץ תיקון אבטחה
> לפני שיש מה להגן עליו. נשמר גם כזיכרון (`esop_defer_security_debt_
> until_customer_facing.md`) כדי שההצעה לא תעלה שוב בשיחה עתידית.

**שני פריטי החוב שהיו רשומים כאן (איחוד `TABLE_REGISTRY`/`_TABLE_SPECS`,
חלון אישור פר-סוג-מסמך) נסגרו בשיחה הזו** - ראו הבלוק למעלה. מקומם פנוי.

**שני פריטי חוב, נסקרו מחדש ונדחו במפורש בשיחה הזו (המשתתף ראה את ההיקף
המלא ובחר לדחות - לא נצברו בשקט):**

1. **ניתוק בין מימוש אופציות לטבלת ההון.** אישור `ExerciseRequest` לא יוצר
   `ShareIssuance`/`Shareholder` - הכרעה מוצרית מפורשת מ-v1.0.0 (מונעת
   ספירה כפולה בנוסחת ה-fully-diluted). product-expert ציין בסקירת v1.0.2
   שאין היום מסך/צורך מאומת ל"מי באמת מחזיק מניות כולל עובדים שמימשו",
   וש-מימוש נכון דורש סוג אירוע ledger חדש (ראו פריט 2 למטה) + עמודת מעקב
   על `ShareIssuance` + החלטה על נתונים היסטוריים. הוצג למשתתף - נדחה
   במפורש לסבב הזה. סיכון 8 ב-`docs/qa/v1.0.0.md`.
2. **`ShareIssuance` נשאר בעל סוג אירוע `ledger` יחיד** (סיכון 3, ללא שינוי
   בהחלטה) - `compute_cap_table_snapshot` מסנן ישירות (`issue_date <=
   as_of`) במקום `project()` per-row, שקול ל-replay מלא רק כל עוד הסיכון
   הזה פתוח. תלוי בפריט 1: היה אמור לשמש כתשתית-קדימה לפני שמימוש מתחיל
   ליצור `ShareIssuance` בפועל - מאחר שפריט 1 נדחה, אין היום צורך עסקי
   בסוג אירוע שני, ונדחה גם הוא במפורש (לא רק "נדחה כי פריט 1 נדחה" -
   נשאל בנפרד ואושר). אם/כשיתווסף (תיקון/ביטול/העברה), חובה לעבור ל-
   `project()` ב-`services/cap_table.py`, אחרת חישוב הדילול יתעלם בשקט
   מהתיקון.

**לקח מ-v1.0.2, שווה לזכור בפעם הבאה שמוסיפים טבלה חדשה עם `company_id`:**
`company_scope.TABLE_REGISTRY`/`SPECIAL_CASED_TABLES` ואינווריאנט
`test_every_company_scoped_table_is_registered_or_explicitly_special_cased`
(`test_project_invariants.py`) קיימים בדיוק בשביל זה - הוסף לטבלה הרלוונטית
ותן ל-`pytest` להצביע אם משהו נשכח, אל תסמכו על זיכרון.

לפי "איך סוגרים שיחה" למטה: מקרי הבדיקה מעודכנים (✅ `docs/qa/v1.0.0.md`,
`QA-100-42`..`QA-100-49`), הסוויטה ירוקה (✅ 381), הקובץ הזה מעודכן
(✅ עכשיו), commit + push + מיזוג + תיוג בוצעו (✅ `main` על `9ef5d65`,
תג `v1.0.0` על `origin`). **השיחה הזו סגורה במלואה.**

**משימה #12 הושלמה (עדכון `docs/qa/v0.9.1.md`).** סעיף `(א2) מקרי בדיקה —
שלב ב` חדש, `QA-091-39` עד `QA-091-86`, מחולק לפי משימה (מס על מימוש #2,
ייצוא #3-5, ייבוא-דריי-ראן #6, ייבוא-commit #7-8, היסטוריה+דוח התאמה #10,
עומק שירות ההתאמה #9, UI #11) - כל שורה נבדקה מול שם פונקציית `pytest` אמיתי
בקבצי הבדיקה, לא רק מול תיאור `PLAN.md`. טבלת הסיכונים עודכנה: סיכון 8
("שלב ב טרם נבנה") נסגר במקום, ונוספו שבעה סיכונים אמיתיים שנחשפו במהלך
הבנייה (14-20) - היקף ההתאמה לא כולל ledger/`OptionPool` (E), שני צדי
ההתאמה חולקים את קבוע 2006 הלא-מתוקן (11) ולכן לא יתפסו את אותה טעות, סיכון
הסוואה של "skip-if-exists" (16), ייבוא CSV לא נתמך (17), `paused_days_total`
לא מחושב מחדש כמו יתרת הפול (18), ה-endpoint של דוח ההתאמה כותב תוך כדי GET
(19, אותו דפוס כמו סיכון 9), וא-סימטריה בין שלושה endpoints בבדיקת קיום קובץ
בדיסק (20). `pytest tests/test_project_invariants.py` (14/14) והסוויטה המלאה
(332, ללא שינוי) - שניהם ירוקים.

**משימה #11 נסגרה (UI - טבלת היסטוריה + מודלים בפורטל האדמין).**
`clients/admin_portal/index_manage.html`: לשונית `<section
id="tab-export-import">` חדשה (לפי צורת `tab-documents`) + קובץ חדש
`clients/shared/export_import.js` (`directionBadge`/`runStatusBadge`/
`rowsSummary`/`matchCell`/`mismatchValue`, מייבא
`ESOPDocuments.escapeHtml`/`.orDash`/`.formatTimestamp`/`.errorDetail`/
`.downloadDocument`). שני מסכים לפי §5 ב-PLAN.md: היסטוריה (states
`loading→list→error`, סינון direction/status, פעולות לפי כיוון) ופאנל
פעולה אחד שמשרת גם ייצוא וגם ייבוא (states `idle→uploading→
dry-run-running→dry-run-shown→committing→done|failed`) - "Done" מפרט
בדיוק מה אומת (למשל "163/163 שורות יוצאו... ראו דוח התאמה") ולא "הצליח".
בכוונה **לא** נוסף כפתור "בצע ייבוא" על דריי-ראן ישן מתוך טבלת ההיסטוריה
(אף שהשרת מאפשר את זה טכנית) - §5 מגדיר זרימה חד-כיוונית בתוך מודל אחד,
לא חידוש commit בין שיחות; פורט מפורשות ב-`PLAN.md` כדי שלא יתערבב עם
scope creep. **אומת ידנית מקצה לקצה מול סנדבוקס חי** (יצוא→הורדה→העלאה
חוזרת כדריי-ראן→commit→דוח התאמה, כולל שני מסלולי כשל - JSON פגום ו-FK
שבור שחוסם commit) - אפס שגיאות קונסולה. **סקירה עצמאית (change-reviewer,
11/08/2026): `REPAIR`**, שני ממצאים תוקנו לפני סגירה: הערת "אין פירוט
שמור" במודל "פרטי ריצה" הוגבלה ל-`IMPORT_DRY_RUN` בלבד והושלמה גם ל-
`IMPORT_COMMIT` (אותה מגבלה בדיוק, ראו task #8), וסדר `escape`/`slice`
הפוך על `entity_id` בטבלת אי-ההתאמות תוקן (escape רק *אחרי* הקיצוץ, לא
לפני). פירוט מלא ב-`PLAN.md` ("Implementation notes added during task
#11").

## מה נבנה ב-11/08/2026 (v1.0.0 שלב א - הושלם)

תוכנית מלאה ב-`C:\Users\Shalom\.claude\plans\parallel-jingling-bear.md`.
תמצית:

- **מודל דאטה (3 טבלאות חדשות):** `ShareClass` (name/class_type/
  seniority_order, בלי `UNIQUE` על הסדר - החלטה עסקית מוצהרת), `Shareholder`
  (`company_id` NOT NULL - חד-חברתי בכוונה, זה מה שפותר ארכיטקטונית את
  חשש ה-IDOR שהועלה בסקירת אבטחה בתכנון; `employee_id` nullable לתמיכה
  במשקיעים חיצוניים), `ShareIssuance` (**ledger-native מהיום הראשון** -
  ההחלטה הארכיטקטונית המרכזית שכל המומחים התכנסו עליה, כדי ש-snapshot
  היסטורי בשלב ב יהיה אפשרי מבנית ולא ידרוש מיגרציה שוברת). שתי עמודות
  nullable על טבלאות קיימות: `Company.total_authorized_shares`,
  `OptionPool.share_class_id` (nullable כי אין ערך להמציא לחברות/פולים
  קיימים שנזרעו לפני v1.0.0).
- **Ledger:** `SHARE_ISSUANCE_ESTABLISHED` נוסף ל-`LEDGER_EVENT_TYPES`,
  `"ShareIssuance"` ל-`LEDGER_AGGREGATE_TYPES`, `project_share_issuance`
  נרשם ב-`PROJECTORS`. מספר-פולים לא דרש סוג אירוע חדש - פול נוסף ממחזר
  את `POOL_BALANCE_ESTABLISHED` הקיים, חי (`source=LIVE`) ולא backfill.
- **API:** `POST`/`GET /admin/pools` (חדש - עד כה רק `seed_data.py` יצר
  פול), ראוטר חדש `backend/app/api/cap_table.py` עם `POST`/`GET` לכל
  אחת משלוש הישויות, הרחבת `PUT /admin/company` ל-`total_authorized_shares`
  (סמנטיקת "לא נגיעה" ל-`None`, לא איפוס), הרחבת `audit.py` לענפי
  `OptionPool`/`ShareClass`/`Shareholder`/`ShareIssuance`. תקרת
  `total_authorized_shares` נבדקת מול **סכום** ההנפקות הקיימות, רק כשהערך
  לא `None`, גבול `>` לא `>=` (שוויון מותר).
- **שני באגים אמיתיים נמצאו ותוקנו בסקירת ה-QA של השלב הזה**, שניהם
  ב-`create_shareholder` (`backend/app/api/cap_table.py`): (1) `employee_id`
  ששייך לעובד של חברה **אחרת** עבר בשקט עם `200` - IDOR, אין בדיקת
  `company_id`. (2) `employee_id` שלא קיים בכלל קרס ב-`IntegrityError` לא
  מטופל (500 גולמי במקום 404 נקי) - אין בדיקת קיום מוקדמת לפני ה-`INSERT`.
  שני התיקונים מראים את אותו דפוס בדיקה שכבר קיים ב-`create_share_issuance`
  (בדוק קיום → בדוק שיוך לחברה → 404/403 מוקדם, לפני כל כתיבה). נעולים
  ב-`tests/test_cap_table.py::test_create_shareholder_linked_to_another_companys_employee_is_403`
  ו-`test_create_shareholder_with_unknown_employee_id_is_404_not_500`.
- **מיגרציה `bd65db40f654`** - אדיטיבית טהורה, בלי `UPDATE`/מחיקה על
  שורות קיימות. **אומתה בפועל, לא הונחה**: מחזור מלא `upgrade head` →
  `downgrade -1` → `upgrade head` על sandbox נפרד (לא `esop_database.db`),
  אפס שגיאות, `alembic current` נכון בכל שלב.
- **בדיקות:** `tests/test_cap_table.py` חדש (כיסוי מלא לארבעת ה-endpoints:
  happy path, cross-company 403/404, positivity, תקרת מניות, audit log,
  ledger replay-equivalence ל-`ShareIssuance`). `tests/test_export.py`
  תוקן (עמודה חדשה `total_authorized_shares` בייצוא - רגרסיית בדיקה,
  לא באג ייצור). הסוויטה המלאה: **372 עוברות, 0 נכשלות** (הייתה 371+1
  נכשלת-בכוונה שחסמה עד ל-bump ה-`VERSION`).
- **`docs/qa/v1.0.0.md` חדש** - `QA-100-01` עד `QA-100-40`, טבלת סיכונים
  עם 7 סיכונים (אין חישוב דילול, אין UI, סוג אירוע ledger יחיד ל-
  `ShareIssuance`, טבלת ההון חסרה מייצוא/ייבוא v0.9.1, אין RBAC דק יותר
  מ-`COMPANY_ADMIN`, `seniority_order` בלי `UNIQUE`, `nullable=True` על
  שני שדות חדשים). `QA_TESTBOOK.md`: v1.0.0 סומן פעיל, v0.9.1 עבר לארכיון.
- **סקירה עצמאית (change-reviewer, 11/08/2026): `PASS`, אפס חוסמים.**
- **`VERSION` → `1.0.0`** (release-manager, אני, 11/08/2026) - הצעד
  המסיים של שלב א, בדיוק כמו התקדים ב-v0.9.1. **אין תג `v1.0.0`** - ימתין
  לסוף שלב ב, כנדרש.
- **6 commits נפרדים** על `feat/1.0.0/database`, **לא נדחפו**:
  `1971b03 feat(cap-table): add ShareClass/Shareholder/ShareIssuance models and ledger integration`,
  `0e726ed feat(api): add multi-pool and cap-table CRUD endpoints`,
  `a3956a2 fix(cap-table): validate employee_id ownership on shareholder creation`,
  `7eed45b test(cap-table): add coverage for pools/share-classes/shareholders/share-issuances`,
  `23a8129 docs(qa): open v1.0.0 phase A test cases and risk table`,
  `835cc68 chore(release): bump VERSION to 1.0.0 for v1.0.0 phase A`.

**מה לא בוצע כאן, בכוונה:** מיזוג ל-`release/1.0.0`/`main`, יצירת תג,
push, ותכנון שלב ב. אלה ממתינים לשיחה הבאה ולהחלטת המשתתף.

## מה נבנה ב-10-11/08/2026 (שלב ב, משימות 1-13 מתוך 13 - הושלם)

פירוט מלא ונימוקים ב-`PLAN.md` (סעיפי "Implementation notes" אחרי כל
משימה). תמצית למי שלא פותח את הקובץ המלא:

- **#1 סכמה:** טבלאות `exercise_tax_records` ו-`data_transfer_runs` חדשות
  (מיגרציה `d9e4f1a2b3c6`).
- **#2 מס על מימוש אמיתי:** `_decide_exercise_request` מחשב וכותב
  `ExerciseTaxRecord` בפועל, לא רק ב-`/simulate-exercise` כמו קודם. **תקלה
  אמיתית נתפסה תוך כדי:** הניסוח הראשון (`business_today()` בזמן האישור)
  שבר את `test_the_clock_is_never_the_source_of_a_tax_date` — תאריך מס
  חייב לבוא ממסמך/פעולה, לא משעון (אותו עיקרון בדיוק כמו ח1/ח2). התיקון:
  `business_date_of(req.requested_at)` — תאריך **הבקשה של העובד**, לא יום
  האישור.
- **#3-5 ייצוא:** `POST /admin/export` + `GET .../download` (JSON/CSV,
  היקף מלא של 13 טבלאות + חבילות מס לפי natural key, לא `pack_id` שהוסר
  מהם בכוונה). מגבלת שורות (`EXPORT_MAX_ROWS`, ברירת מחדל 50,000) נבדקת
  *לפני* הקריאה היקרה, לא אחריה.
- **#6 ייבוא דריי-ראן:** `POST /admin/import/dry-run` מסווג כל שורה
  NEW/SKIP_EXISTING/ERROR מול היעד — **לא כותב שורת דומיין אחת**. הבהרה
  ארכיטקטונית שלא הייתה מפורשת קודם: ייבוא **תמיד** נכתב תחת החברה
  הקיימת של המאשר (`current_user.company_id`) — שורת `companies` בחבילה
  משמשת רק לבדיקת סבירות, לעולם לא נוצרת חברה חדשה ממנה.
- **#7 ייבוא commit:** `services/import_.py::commit()` בלבד (לא endpoint -
  הוחלט במפורש, ראו "הצעד הבא") — מריץ `dry_run` מחדש מול ה-bundle שהתקבל
  (לא סומך על דוח ישן), כותב הכל-או-כלום בסדר הטופולוגי הקיים, מאפס כל
  `*_user_id` (users לעולם לא מיובאת), פותר `pack_id` של חבילות מס מחדש
  לפי natural key (הוא לא בקובץ בכלל — הוסר בייצוא), בונה `LedgerOwnership`
  מחדש (`record_ownership`, לא נכתב ישירות מ-`append_event`), ומחשב מחדש
  `OptionPool.allocated_shares`/`unallocated_shares` פעם אחת אחרי כל הבאטש
  כדי שפול קיים שקיבל אירועים היסטוריים חדשים לא יישאר עם יתרה מיושנת.
  שבע בדיקות חדשות ב-`tests/test_import_commit.py`; הסוויטה המלאה עברה
  אחרי (307, היו 300). פירוט מלא, כולל שני הממצאים שאושרו מול המשתתף, ב-
  `PLAN.md` ("Implementation notes added during task #7").
- **#8 ייבוא commit endpoint:** `POST /admin/import/commit` — שני שלבים,
  `{dry_run_id}` בלבד. 409 על דריי-ראן `COMMITTED`/`FAILED`, וגם על דריי-ראן
  שהיה תקין אך ה-DB השתנה מתחתיו (מתגלה כי `commit()` מריץ `dry_run` מחדש,
  לא ברמת ה-endpoint). הצלחה מסמנת את הדריי-ראן `COMMITTED` וקושרת
  `DataTransferRun` חדש (`IMPORT_COMMIT`) אליו דרך `based_on_run_id`. שש
  בדיקות חדשות; הסוויטה המלאה: 313 (היו 307).
- **#9 שירות ההתאמה (reconciliation), service-layer בלבד:**
  `services/reconciliation.py::reconcile(db, bundle, as_of=None)`. הבשלה:
  משווה `calculate_vested_options` על אובייקטים זמניים מה-bundle (לא
  נכתבים ל-DB) מול הרצה על שורות היעד בפועל, לפי `as_of` משותף ומוצהר -
  לא שעון. מס: לוקח `ExerciseTaxRecord.gain` שכבר נשמר (task #2), גוזר
  `exercise_date` מ-`business_date_of(request.requested_at)` (אותו כלל
  כמו אישור אמיתי - decision B), ומריץ מחדש נגד חבילות המס *של היעד* -
  משווה גם `effective_rate`/`table_effective_date`/`method`, לא רק סכום.
  שתי הכרעות מוצר אושרו מראש (ר' `PLAN.md` §"Decisions resolved", E/F):
  היקף ההבשלה צר ומכוון (בלי `vesting_cutoff_date`/הקשר עובד - האובייקט
  הזמני מה-bundle אין לו), וההשוואה אחידה על כל שורה בלי לשחזר סיווג
  NEW/SKIP_EXISTING שאבד אחרי commit - המחיר מוצהר ב-`known_limitations`
  של הדוח (יחד עם ledger skew מ-§7 סיכון 1 ועוגן 102 קדם-2006 מ-HANDOFF).
  תשע בדיקות חדשות ב-`tests/test_reconciliation.py`, כולל בדיקת
  "שורת יעד מזויפת" (משנה `VestingSchedule`/`TaxRatesHistory` ביעד אחרי
  commit ומוודאת שההתאמה מזהה זאת, לא משווה שורה לעצמה); הסוויטה המלאה:
  322 (היו 313). **סקירה עצמאית (change-reviewer, 11/08/2026): `PASS`**,
  שלושה ממצאים זעירים תוקנו לפני סגירת המשימה - שדה mismatch שדיווח תמיד
  `tax_amount` גם כשהשוני היה ב-method/rate/date בלבד (עכשיו כל שדה שסטה
  מדווח בנפרד עם הערך הנכון), ענף `except MissingVestingScheduleError` מת
  (הוסר - שני התנאים שקדמו לו כבר שוללים את המקרה היחיד שהיה מפעיל אותו),
  וכיסוי בדיקה חסר לענף ההגנתי של `ExerciseRequest` חסר ביעד (נוסף). פירוט
  מלא ב-`PLAN.md` ("Implementation notes added during task #9").
- **#10 endpoints היסטוריה + דוח התאמה:** `GET /admin/export-import/
  history` (`?direction=`/`?status=`, 400 על ערך לא מוכר, scoped לפי
  source_company_id **או** target_company_id - המסך היחיד שמציג ייצוא
  וייבוא יחד) ו-`GET /admin/export-import/{run_id}/reconciliation`
  (בונה בזמן אמת - `reconcile()` הוא פונקציה טהורה בלי מצב שמור; 404 על
  run שאינו `IMPORT_COMMIT`, 403 חוצה-חברות, **500** על bundle חסר -
  לא 404 כמו שנוסח לראשונה). שום router/מיגרציה חדשים. **סקירה עצמאית
  (change-reviewer, 11/08/2026): `REPAIR`** - תפסה קריסה אמיתית
  (הניסוח הראשון טען 404 על bundle חסר בלי לבדוק את הדיסק בפועל,
  ובפועל קרס ב-500 לא-מטופל כשהקובץ נמחק) ושני פערי בדיקה (בדיקת ה-500
  הזו לא הייתה קיימת, ואף בדיקת HTTP לא בדקה דוח *לא*-נקי בפועל, רק את
  המסלול הנקי) - שניהם תוקנו לפני commit, לא נדחו לסבב הבא. עשר בדיקות
  HTTP-level (19 בקובץ בסך הכל); הסוויטה המלאה: 332 (היו 322). פירוט
  מלא ב-`PLAN.md` ("Implementation notes added during task #10").
- **#11 UI - היסטוריה + מודלים בפורטל האדמין:** לשונית חדשה + מודל פעולה
  אחד לייצוא/ייבוא + מודל דוח התאמה - פירוט מלא ב"הצעד הבא" למעלה. ללא
  שינוי למספר הבדיקות (332 - frontend בלי backend, אין סוויטת pytest
  חדשה) - אומת ידנית מקצה לקצה מול סנדבוקס חי.
- **#12 עדכון `docs/qa/v0.9.1.md` - פירוט מלא ב"הצעד הבא" למעלה.** סעיף
  (א2) חדש (`QA-091-39` עד `QA-091-86`) + סגירת סיכון 8 + סיכונים 14-20
  חדשים. תיעוד בלבד - אפס שינוי קוד, 332 בדיקות ללא שינוי.
- **#13 `VERSION` (release-manager, 11/08/2026) - צעד הסיום של שלב ב.**
  `VERSION` נשאר `0.9.1` ללא שינוי - הוא כבר עודכן לערך הזה בשלב א
  (`a86bfea`), ו-`test_version_file_is_not_behind_the_qa_testbook` דורש
  `VERSION >= newest QA doc` ולא שוויון, כך שזה כבר עבר טרם המשימה; אף
  מסמך בתוכנית לא דרש מספר גרסה נוסף. מה שבוצע בפועל: שורת v0.9.1
  ב-`QA_TESTBOOK.md` עודכנה לתאר את שני השלבים כהושלמו (סטטוס "פעילה"
  נשאר - הטבלה משתמשת בו לגרסה החדשה ביותר עד שגרסה עוד יותר חדשה
  נכנסת מעליה, לא כתגית "בבנייה"), ותג annotated `v0.9.1` נוצר על ה-HEAD
  (`066766c`) - מקומי, לא נדחף. הסוויטה המלאה אחרי: 332 (ללא שינוי).

**שני "לקחים" ששווה לזכור לפני שממשיכים ל-#7:**

1. SQLAlchemy לא מבטיח סדר INSERT בין שתי טבלאות שחולקות FK גולמי בלי
   `relationship()` (למשל `TaxRulePack`/`TaxRatesHistory`) — צריך `flush()`
   מפורש ביניהן, גם ב-fixtures של בדיקות.
2. התנגשות חוצה-חברות בייבוא מדווחת כשגיאת-שורה בתוך דוח 200 רגיל, **לא**
   כ-409 ברמת ה-endpoint כמו שתוכנית ה-API המקורית תיארה — סטייה מכוונת,
   מתועדת ב-`PLAN.md`.

## מה נסגר ב-09/08/2026 (הסבב השני)

ארבעה פריטים שכולם היו רשומים כאן כפתוחים. מקרי הבדיקה: `docs/qa/v0.9.1.md`,
`QA-091-24` עד `QA-091-38`.

- **ח3 · `init_scheme.sql` הושלם.** הפער נמדד ולא שוער: `employees.national_id`,
  שלוש עמודות נעילת החשבון ב-`users` (עם ה-`DEFAULT` שהמיגרציה חייבת כדי
  להוסיף NOT NULL על טבלה מאוכלסת), `pack_id` + FK בשתי טבלאות המס, ושלושה
  אילוצי UNIQUE. אחרי התיקון: אפס דריפט.
- **ח4 · האינווריאנט משווה עכשיו עמודה-עמודה** דרך `PRAGMA` — טיפוס, `NOT NULL`,
  מפתח ראשי, מפתחות זרים ואילוצי UNIQUE. `PRAGMA` ולא טקסט DDL בכוונה: אילוץ
  בשורת העמודה ואילוץ בסוף הטבלה זהים ל-DB ושונים כטקסט. אומת שאינו ריק —
  הקובץ הישן הוחזר ב-`git stash` והבדיקה נפלה, בעוד הבדיקה הישנה עברה.
- **`termination_date` — החוב נסגר.** `DELETE /admin/employees/{id}` דורש
  `?termination_date=` כשלעובד יש מענקים; בלעדיו `400` והעובד נשאר `ACTIVE`.
  מחיקה מלאה (בלי מענקים) לא השתנתה. פורטל האדמין מבקש את התאריך רק אחרי
  שהשרת החזיר 400, ולכן הוא לא שואל על עובד שייעלם ממילא.
- **`EXPIRED` — הוכרע: 30 יום מהשליחה.** ראו הסעיף הבא.
- **עוגן חלון 102 — הוכרע והוטמע.** ראו הסעיף הבא.

## ממצאי הסקירה העצמאית (09/08/2026)

ארבעתם אומתו ידנית מול הקוד, לא רק מדיווח הסוכן.

### ✅ ח1 + ח2 — נסגרו 09/08/2026

שניהם היו **אותה תקלה**: שלב א העביר גבולות מזכים ו-`effective_date` מ-
`date.today()` ל-UTC. ישראל *לפני* UTC, ולכן בין 00:00 ל-03:00 בירושלים תאריך
ה-UTC הוא אתמול — חלון מימוש שנשאר פתוח יום מיותר (ח1), ו-`effective_date`
שנרשם בשנת מס קודמת בטבלה append-only שאין בה UPDATE (ח2).

**התיקון אינו החזרה ל-`date.today()`.** זה היה מחזיר את התלות במארח, ושובר
בדיקה קיימת. במקום זה נוסף שעון שלישי: `business_today()` ב-`types.py`, קשור
לאזור זמן עסקי **מפורש** (`Asia/Jerusalem`, ניתן לעקיפה ב-
`ESOP_BUSINESS_TIMEZONE`) ולא לזה של המארח. `types.py` כבר צפה את השם הזה.

- **11 אתרים** הועברו לשעון העסקי: 8 ב-`routes.py`, 3 ב-`notifications.py`.
- **גם `termination_date`** (`routes.py:477`) — הוא *מזין* את דדליין החלון,
  והשארתו על `date.today()` הייתה מותירה את שני צדדי אותו חישוב על שעונים
  שונים. החוב עצמו (קלט מפורש) **לא נסגר** — ראו סיכונים.
- **`recorded_at` נשאר UTC בכוונה.** זו ההבחנה הבי-טמפורלית: ידיעה מול תוקף.
  שלב א איחד אותם לשעון אחד ומחק אותה.
- **`tzdata` נועץ ב-`requirements.txt`** — חובה, ל-Windows אין מסד אזורי זמן.
  כשל התצורה מפורש ב-import ולא נפילה שקטה ל-UTC.
- **8 בדיקות חדשות** (`tests/test_business_day_clock.py` + אינווריאנט).
  אומת שהן אינן ריקות: הרגרסיה הוחזרה זמנית ו-4 מהן נפלו. שתי בדיקות
  אינווריאנט **חוזקו** ולא הוחלשו — החריג של `date.today()` נסגר לגמרי.

**מה הסקירה העצמאית מצאה (`PASS`, אפס חוסמים):** ההרחבה עצמה יצרה גבול חדש —
`_rule_request_pending_too_long` השווה `requested_at` (UTC) מול `today` (עסקי),
כלומר ספר יום המתנה מיותר. תוקן ב-`business_date_of()`, עם בדיקה שנופלת בלעדיו.
בנוסף חוזק האינווריאנט (`utcnow().date()` היה חומק ממנו) ותועד שהשורות
ההיסטוריות ב-`ledger_events` נשארות על השעון הישן ואינן ניתנות לתיקון.

### ח3 + ח4 — נסגרו 09/08/2026

ראו "מה נסגר" למעלה. הלקח שנרשם: **בדיקה שירוקה מול הבאג שהיא נכתבה כדי לתפוס
גרועה מאין בדיקה** — היא מסירה את הדריכות. תיקון (2) של `init_scheme.sql` הוסיף
רק את הטבלאות החדשות ולא בנה מחדש את הישנות, והאינווריאנט ברמת שמות טבלאות
אישר את התוצאה.

### `EXPIRED` — הוכרע 09/08/2026: 30 יום מהשליחה

הכרעת מוצר, לא כלל מס — אין ב-102 תקופת תוקף לבקשת אישור. שלוש בחירות בתוך
ההכרעה, וכל אחת מהן היא מה שיישבר אם מישהו "יפשט" אותה:

- **`expires_at` הוא חותמת זמן ולא תאריך.** `sent_at + 30 יום` הוא רגע פיזי,
  ולכן `utcnow() > expires_at` נכון בכל אזור זמן. גבול שנמדד בימים קלנדריים
  היה מחזיר את שאלת ח1/ח2 דרך הדלת האחורית.
- **טאטוא עצל, לא scheduler.** ההפקעה קורית בכל נתיב שטוען מסמך, בנקודת חנק
  אחת (`_documents_out`) ולא בכל endpoint לחוד — הוספה לכל אחד בנפרד היא בדיוק
  P3. מצב ה-DB ומה שמוצג תמיד מסכימים.
- **`expires_at = NULL` הוא "אין דדליין", לא "פג".** מסמכים שנשלחו לפני v0.9.1
  נשארים פתוחים. הפירוש ההפוך היה מפקיע כל בקשה פתוחה ברגע השדרוג.

מיגרציה `c8d5e2f0a1b4` מוסיפה את העמודה **ומרחיבה את טריגר ההקפאה** כך ש-
`expires_at` קפוא על מסמך מאושר. שלושת הפורטלים מציגים ספירת ימים — מותר
עכשיו, כי היא נגזרת מערך מאוחסן. **סיכון 8 של v0.9.0 נסגר.**

### עוגן חלון 102 — הוכרע 09/08/2026

**הבקשה המקורית ("מיום ההקצאה") נבדקה וחזרה שגויה.** לשון 102(א) היא
"24 חודשים מיום שבו הוקצו המניות **והופקדו** בידי נאמן" — היום שבו התקיימו
*שני* התנאים. ההגדרה המשלימה דורשת הפקדה במועד ההקצאה, כלומר המחוקק מניח
ששני התאריכים זהים. המשתתף אישר את הניסוח הזה.

`engine.py` מחשב `max(grant_date, trustee_deposit_date)`. **התוצאה זהה
להתנהגות הקודמת** — ה-API אוסר הפקדה לפני הענקה מאז v0.6.0 — אבל האילוץ הזה
הוא כלל *קלט* שאפשר לשנות, והכלל המסי אינו תלוי בו.

**למה זה לא היה "הפרש של 30 יום":** לפי 102(ב)(4), מימוש לפני תום התקופה מסווג
את *מלוא* שווי ההטבה כהכנסת עבודה בשיעור שולי במקום 25%. ספירה מ-`grant_date`
לבדו הייתה מייצרת מספר תקין למראה ונמוך מהחבות האמיתית, במסמך שהעובד מסתמך
עליו. **היפוך סיווג, לא הפרש ימים.**

**נמצא תקין ואומת (לא לפתוח מחדש):** `UtcDateTime` אכן דוחה naive וממיר ל-UTC,
ו-`process_result_value` משתמש ב-`replace` ולא ב-`astimezone` — הנכון. הטענה
שלא נדרשה מיגרציית דאטה אומתה מול ה-DB החי בקריאה בלבד: כל 1035 שורות
`ledger_events.recorded_at` וכל שאר העמודות מאוחסנות בפורמט זהה למה שה-bind
processor מייצר. חיזוק `TaxCalculationResult` הוא החלק החזק בשינוי. **אף בדיקה
לא הוחלשה ולא נמחקה.**

**פערי כיסוי שנותרו:** `ensure_utc` על `knowledge_date` נבדק ברמת השירות ולא
ברמת ה-HTTP — מחיקת `routes.py:672` משאירה את הסוויטה ירוקה. (פער השעונים
נסגר: 11 האתרים מכוסים עכשיו בהתנהגות ובאינווריאנט, לא ב-grep.)

## הכרעות שהתקבלו ב-v0.9.0 (לא לפתוח מחדש)

- **"אישור קבלה", לא "חתימה".** למערכת אין אימות זהות, הצפנה או גורם שלישי.
  המילים `signature`/`signed`/"חתימה" לא מופיעות באף שדה, כפתור או תווית —
  רק בהצהרות "אינו חתימה". `FEATURE_SPEC.md` תוקן בהתאם (הוא הבטיח "חתימה
  דיגיטלית" עד v0.9.0).
- **דחייה בלי שדה סיבה.** `DECLINED` אינו נושא סיבה. נדחה במפורש; רשום כסיכון 10.
- **גרסה מיושנת לא עוברת שום מעבר מצב**, גם לא אישור — נאכף בשרת
  (`assert_is_current_version`), לא רק במסך.
- **הנייר הישן תמיד נשמר, ותמיד אפשר להוציא נייר חדש.** טריגר ההקפאה מגן על
  *תוכן* האישור בלבד ומאפשר `is_latest` להשתנות (מיגרציה `b7c4d1e9f2a3`).

## חוב פתוח

**פתוח מ-v1.0.0 שלב א (11/08/2026) - שני פריטים לשיחה הבאה** (הפריט השלישי,
DB חי בסטייה, **נסגר 11/08/2026** - ראו ההערה בראש הקובץ; המיגרציה `bd65db40f654`
תוקנה בעקבות כשל אמיתי מולה, `esop_database.db` עלה ל-head):

- ⬜ **שלב ב (חישוב דילול + UI) טרם תוכנן.** `parallel-jingling-bear.md`
  מכיל רק את היקף שלב א; שלב ב דורש מחזור תכנון מלא (5 מומחים read-only),
  ומתחיל מהגדרת "fully-diluted מול outstanding" - שאלה מוצרית פתוחה
  שהתוכנית המאושרת מציינת ולא מכריעה.
- ⬜ **ייצוא/ייבוא (v0.9.1) לא כולל את טבלאות ההון החדשות.** `ShareClass`/
  `Shareholder`/`ShareIssuance`/`OptionPool.share_class_id`/
  `Company.total_authorized_shares` נעדרים מרישום הטבלאות ב-
  `services/export.py`. מתועד כסיכון 4 ב-`docs/qa/v1.0.0.md`, מחוץ להיקף
  שלב א/ב במפורש - לא באג, אבל לא נסגר.

**נסגר ב-v0.9.1 שלב א (09/08/2026):**

- ✅ `datetime.utcnow()` — 18 אתרים. `UtcDateTime` (TypeDecorator) ב-
  `backend/app/types.py`: ממיר ל-UTC בכתיבה, **דוחה naive**, מחזיר aware בקריאה.
- ✅ **הגבול הרביעי** — `ensure_utc` על `knowledge_date` ב-`routes.py`. זה היה
  הכשל האמיתי, לא העמודות.
- ✅ `date.today()` → `system_today_utc()` ב-11 אתרים. אתר אחד נשאר במכוון.
- ✅ `conftest.py` — `alembic upgrade head` במקום `create_all`. 4 הטריגרים
  פעילים בכל הסוויטה, ו-`create_schema` נכשל אם לא נוצר אף טריגר.
- ✅ ה-`SAWarning` באותו קובץ.
- ✅ `init_scheme.sql` — היו חסרות בו **4 טבלאות וכל 4 הטריגרים**. הוא עצר
  ב-0.5.0 בזמן ש-`CLAUDE.md:17` מפנה אליו כמקור אימות. נאכף עכשיו בבדיקה.
- ✅ `class Config` → `ConfigDict` (7) · `db.query().get()` → `Session.get()`.
- ✅ `TaxCalculationResult` — הפער לא היה האריתמטיקה (מכוסה היטב) אלא
  **שרשור המקורות**: `source_url` לא נבדק מעולם, `pack_id` רק כ-`is not None`.

**ארבעה אינווריאנטים חדשים** ב-`tests/test_project_invariants.py` מונעים נסיגה.

**מה נלמד — לא לחזור על זה:**

- **"עמודות aware" ב-SQLite הוא תיקון שגוי.** SQLAlchemy משמיט tzinfo בשקט
  ו*אינו* ממיר. `DateTime(timezone=True)` לא משנה דבר. הוא היה מכניס שגיאת
  3 שעות ל-`recorded_at`.
- **לא נדרשה מיגרציית דאטה.** כל הערכים כבר UTC, ופורמט האחסון לא השתנה —
  ולכן גם לא היה עימות עם `trg_ledger_events_no_update`.

**סיכונים אמיתיים שנותרו:**

- **אזור זמן עסקי יחיד.** `Asia/Jerusalem` הוא קבוע (עם עקיפה ב-env). לתוכנית
  אמריקאית באותה מערכת אין שעון משלה — ארה"ב מאחורי UTC וישראל לפניו, ואין
  ערך יחיד נכון לשתיהן. **מוסכם וידוע**, לא תקלה, כל עוד המערכת מודלת 102.

- **עוגן 102 אומת ברמת מקור משני בלבד.** שכפול הפקודה במאגר חקיקה מסחרי +
  שלושה מקורות מקצועיים בלתי-תלויים; **נוסח ראשוני לא נפתח.** טעון אישור
  יועץ מס/רו"ח לפני שהפלט משמש לדיווח אמיתי.

- **`TRUSTEE_HOLDING_MONTHS = 24` הוא קבוע גלובלי בלי `effective_start_date`,**
  בעוד הכלל עצמו תלוי-תאריך: לפני תיקון 147 (01/01/2006) הנוסח היה "24 חודשים
  **מתום שנת המס**". מענק היסטורי כזה יחושב היום בכלל הלא נכון. גם מסלול הכנסת
  עבודה (12 חודשים, אותו עוגן) אינו ממודל — הקבוע היחיד הוא 24.

- **הפקדה באיחור ניכר — דינה לא הוכרע.** שתי פרשנויות אפשריות (יציאה מהמסלול
  מול הסדר פרטני), ואף אחת לא אומתה. גם חלונות ההפקדה המנהליים (45/90 יום
  מהחלטת הדירקטוריון) **חסומים ליישום**. אין היום שדה "טעון הכרעת רשות המסים",
  ואין במודל מועד החלטת דירקטוריון — והחלונות נמדדים ממנו, לא מ-`grant_date`.
  מענק כזה מקבל כרגע תאריך מחושב כרגיל, **וזו אינה הכרעה שהתקבלה.**

- **ההפקעה כותבת בתוך נתיב GET** (`_documents_out` מריץ `db.commit()`). המעבר
  חד-כיווני ואידמפוטנטי, ולכן קוראים מקבילים מגיעים לאותה תוצאה — אבל כל שינוי
  אחר שיושב באותו סשן ייכנס ל-commit הזה. היום אין נתיב GET כזה.

### ✅ פיצול `routes.py` — בוצע 10/08/2026

הוכרע 09/08/2026, בוצע ונסקר 10/08/2026. `backend/app/api/routes.py`
(1,507 שורות, 48 endpoints על `APIRouter()` יחיד) הוחלף ב-12 קבצים לפי תחום
(`auth.py`, `employees.py`, `company.py`, `grants.py`, `exercise_requests.py`,
`audit.py`, `ledger.py`, `trustee.py`, `employee_dashboard.py`,
`documents.py`, `notifications.py`, `search_meta.py`), מראה את התבנית הקיימת
ב-`services/`. `exercise_requests.py` הוא הבעלים של 6 helpers חוצי-תחום
(`_vested_at` וכו') ש-`trustee.py`, `employee_dashboard.py` ו-`documents.py`
מייבאים ממנו — ייבוא חד-כיווני, לא מעגלי. פיצול `admin_portal/index_manage.html`
**לא** נכלל — נדחה לגרסה אחרת.

**אומת:** דיפ מדויק של `(path, methods, dependency-callables)` על `app.routes`
לפני/אחרי — `IDENTICAL`, 0 הבדלים. שני קבצי בדיקה שעשו monkeypatch לפי שם
מודול על `routes.py` עודכנו (`test_business_day_clock.py` →
`exercise_requests_module`; `test_documents.py` → `api_documents_module`
בנוסף ל-`services.documents`). שני אינווריאנטים חדשים ב-
`tests/test_project_invariants.py` (path כפול בין ראוטרים, ראוטר שנשכח
מ-`include_router`) — שניהם אומתו שאינם ריקים בהזרקת רגרסיה מכוונת.

**סקירה עצמאית (change-reviewer, 10/08/2026):** `PASS` על הפיצול עצמו — 0
ממצאים על נתיבים/חוזים/לוגיקה שהשתנו בטעות, הערות רציונל אבטחה (IDOR, TOCTOU)
נשמרו מילה-במילה. ממצא חוסם יחיד: ה-diff המקורי כלל את הפיצול **מעורבב** עם
ארבע העבודות האחרות של שלב א (EXPIRED, עוגן 102, `termination_date`, סנכרון
`init_scheme.sql`) ב-commit אחד לא-מחויב. **תוקן**: 5 commits נפרדים ומאומתים
בבידוד (כל אחד עם `pytest` ירוק לבדו, לא רק ביחד) —
`refactor(api): split routes.py` · `feat(documents): expire acknowledgment
requests` · `fix(engine): anchor the Section 102 holding period` ·
`feat(employees): require an explicit termination_date` ·
`test(schema): compare init_scheme.sql column-by-column`.

**אין כרגע הכרעה פתוחה שממתינה למשתתף.**

**חוב רגיל:**

- **אין ולידציה על `termination_date`** מול `hire_date` או מול העתיד — לא
  ב-`DELETE` ולא ב-`PUT .../status`, שכבר היה כך. תאריך שגוי מקליד מזיז דדליין
  מימוש. לא נוסף כאן במכוון: הוספת כלל לנתיב אחד בלבד הייתה יוצרת בדיוק את P3.
- **`holding_period_end_date` מוחזר כ"היום"** למענק עם נאמן ובלי תאריך הפקדה
  (`trustee.py:47`, `employee_dashboard.py:52`, `exercise_requests.py:249`) —
  קורא כ"התקופה מסתיימת היום" לצד `is_met=false`. `documents.py` ו-
  `notifications.py` מוגנים; אלה לא.
- **חלון האישור הוא קבוע יחיד** (30 יום) — לא פר-חברה ולא פר-סוג מסמך.
- קישור מסמכים לישות כמסך ("כל המסמכים של העובד הזה") — תוכנן ל-v0.9.0, לא נבנה.
- הפורטלים הציגו עד היום חותמות UTC כזמן מקומי (`new Date()` על ערך בלי היסט).
  הפלט עכשיו נגמר ב-`Z` והתצוגה תשתנה — **זה תיקון, לא רגרסיה.**

---

## איפה יושב מה

| | |
|---|---|
| כללי הפרויקט | `CLAUDE.md` |
| מודל הסוכנים והברנצ'ים | `AGENT_WORKFLOW.md` |
| דפוסי כשל P1-P6 + הכנת סביבה | `QA_TESTBOOK.md` (אינדקס) |
| מקרי בדיקה לכל גרסה | `docs/qa/<גרסה>.md` |
| אינווריאנטים של הריפו | `tests/test_project_invariants.py` |
| המפרט והיעדים | `FEATURE_SPEC.md`, `GOAL.md` |

## איך סוגרים שיחה

1. `docs/qa/<גרסה>.md` מעודכן (Definition of done ב-`CLAUDE.md`).
2. `python -m pytest` עובר.
3. **הקובץ הזה מעודכן** — מצב, צעד הבא, החלטות פתוחות, חוב.
4. commit + push.
5. שיחה חדשה: `+` ליד `ESOP_test`, שם לפי גרסה. **לא ממשיכים שיחה בין גרסאות.**
