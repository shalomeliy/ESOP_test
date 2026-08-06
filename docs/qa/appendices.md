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
