# מפת משתמשים לבדיקות QA — כל באג עם משתמש ייעודי

סיסמה אחידה לכל המשתמשים: **`Demo1234!`**

מטרת הקובץ הזה: לכל תרחיש/באג ידוע במערכת יש כאן חשבון login קונקרטי שאיתו אפשר
לשחזר אותו דרך הפורטלים בפועל (לא רק לקרוא את הדאטה בקובץ). מקור האמת לדאטה עצמו
הוא `backend/seed_data.py` — הקובץ הזה רק מצביע לאן ללכת.

## קטגוריה A — הרשאות/IDOR (לא תלוי ברשומה ספציפית - כל זוג עובד תקף)

| # | באג | איך לשחזר |
|---|---|---|
| 1 | `admin/employees` מחזיר עובדים מכל החברות, לא רק שלך | התחבר כ-`admin@comp-001.demo`, עבור לטאב "ניהול עובדים" - יופיעו שם גם עובדים עם `company_id` שונה מ-COMP-001 (מסומן בצהוב בטבלה) |
| 2 | `employee/dashboard/{id}` לא בודק בעלות | התחבר כ-`israel@company.com` (EMP-001, COMP-001), פתח את תיבת ה-QA בדשבורד, הזן `employee_id=EMP-BUG-OVERVEST-1` (שייך ל-COMP-BUGS) ולחץ "שלוף" - אמור לקבל 403, בפועל מקבל 200 |

## קטגוריה B — תרחישי דאטה (כל אחד עם עובד ייעודי, יש עכשיו login)

| # | תרחיש | employee_id | username | מה בודקים |
|---|---|---|---|---|
| 3 | קטין עם מענק (מקורי) | EMP-UNDERAGE-1 | underage(email ב-DB) | אין בדיקת גיל מינימלי ב-`/admin/grants` |
| 4 | קטין (מהאצווה של 12) | EMP-MINOR-1, EMP-MINOR-2 | minor1@company.com וכו' | אותו באג, נפח נוסף |
| 5 | פוטר חודש אחרי מענק, פול לא מסונכרן | EMP-GRANT-TERM1M-1 | quickterm1@quickturn.example | `pool.allocated/unallocated` לא עודכן בזמן הפיטורים - חוסר איזון אמיתי מול המענקים שבאמת קיימים |
| 6 | נפטר לפני ה-cliff | EMP-DEC-PRECLIFF-1 | decpre1@company.com | vested=0, ו-PTEW (חלון מימוש לאחר עזיבה) על 365 יום מתאריך הפטירה |
| 7 | נפטר אחרי ה-cliff, עדיין בנאמנות | EMP-DEC-INTRUSTEE-1 | decintrust1@company.com | vested>0 אבל גם חסימת נאמן וגם PTEW פעילים בו-זמנית |
| 8 | פרש/ותיק - ישראל | EMP-RETIRE-IL-1 | retireil1@harkerem.example | אין טיפול ייעודי לפרישה במודל - נשאר ACTIVE |
| 9 | פרש/ותיק - ארה"ב | EMP-RETIRE-US-1 | retireus1@meridian.example | אותו דבר, US_ISO/US_NSO, בלי נאמן |
| 10 | עזב וחזר (Boomerang) | EMP-REHIRE-1 | rehire1@boomerang.example | `termination_date` נשאר היסטורי למרות ACTIVE - PTEW **לא** אמור לחול (בדוק ש-`is_within_post_termination_window=true`) |
| 11 | הבשלה מלאה, לא מומש שנה | EMP-UNEXERCISED-1YEAR | unexercised_1year@patientcap.example | vested=100% הרבה זמן, בלי exercise - האם המערכת מזכירה למשתמש? (כרגע לא) |
| 12 | הבשלה מלאה, ללא לוח הבשלה בכלל (המקורי) | EMP-UNEXERCISED-1 | unex@company.com | ל-grant הזה **אין** VestingSchedule - vested=0 לצמיתות למרות שהמענק ישן |
| 13 | פוטר, ללא חברה בכלל | EMP-NOCOMPANY-1 | nocompany1@orphaned.example | `company_id=NULL` - בדוק תצוגה בכל מסך שמניח שיש חברה |
| 14 | פוטר עם מענק ישן (המקורי) | EMP-RETIRED-1 | retired@company.com | מענק בלי VestingSchedule + סטטוס TERMINATED |
| 15 | נפטר לפני התאריך (המקורי) | EMP-DEC-BEFORE-1 | dec_before@company.com | מענק בלי VestingSchedule + DECEASED |
| 16 | נפטר אחרי התאריך (המקורי) | EMP-DEC-AFTER-1 | dec_after@company.com | אותו דבר |

## קטגוריה C — "מעבדת באגים" (COMP-BUGS) - כל אחד מבודד, בקשת מימוש כבר ממתינה

חברה ייעודית: `admin@comp-bugs.demo` | נאמן ייעודי: `trustee@trustee-bugs.demo`

| # | באג | username | תרחיש מוכן | תוצאה שגויה שכבר אימתנו |
|---|---|---|---|---|
| 17 | אישור בקשה מעל ה-vested | bug.overvest@buglab.example | vested=1300, בקשה PENDING על 4000 (REQ-BUG-OVERVEST-1) | admin מאשר בהצלחה (200) למרות שאין מספיק vested |
| 18 | שתי בקשות PENDING חופפות | bug.duplicate@buglab.example | 2×2000 PENDING על grant של 3000 בסה"כ (REQ-BUG-DUPLICATE-1A/B) | admin מאשר את שתיהן (200+200), סה"כ מאושר 4000>3000 |
| 19 | אישור לפני תום חסימת הנאמן | bug.earlyhold@buglab.example | הפקדה לפני 3 חודשים בלבד, בקשה PENDING על כל ה-vested (REQ-BUG-EARLYHOLD-1) | admin מאשר (200) למרות ש-`is_trustee_holding_period_met=false` עד 2028 |
| 20 | קריסת 29 בפברואר | bug.feb29@buglab.example | לוח הבשלה עם start=29/2/2024, cliff=24 חודשים | `GET /employee/dashboard/EMP-BUG-FEB29-1` מחזיר **500** (נבדק ומאומת) |

הערה: אחרי שתאשר/תדחה בקשה מ-#17-19 היא כבר לא PENDING. כדי לקבל תרחיש נקי מחדש
להדגמה נוספת, הרץ שוב `python backend/seed_data.py` (מוחק ובונה הכל מחדש מאפס).
