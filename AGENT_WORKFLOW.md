# מודל עבודה עם סוכנים — מי עושה מה, ואיך גרסה יוצאת

12 סוכנים, כל אחד אחראי על חלק אחד במערכת, עובד ב-branch משלו, וכל הברנצ'ים
מתאחדים בסוף גרסה. הקבצים עצמם: `.claude/agents/*.md`.

---

## הצוות

### בונה כשאין פתרון קיים
| סוכן | אחריות |
|---|---|
| **builder** | מקים רכיב חסר מאפס — רק אחרי שסוכן אחר לא מצא פתרון חינמי קיים. גם רשאי *לדחות* הסלמה ולהחזיר "יש חבילה שעושה את זה". |

### מתכנתים (כותבים קוד)
| סוכן | בעלות על |
|---|---|
| **backend-engineer** | `routes.py`, `services/`, `auth.py`, `schemas.py`, `main.py` |
| **database-engineer** | `models.py`, `database.py`, `init_scheme.sql`, מיגרציות, אינדקסים, constraints |
| **frontend-engineer** | שלושת הפורטלים ב-`clients/` |
| **integration-engineer** | ייבוא CSV/Excel, API ציבורי, webhooks, סנכרון HR/שכר |
| **reporting-engineer** | דוחות, ייצוא (CSV/Excel/PDF), דאשבורדים, snapshots |
| **security-engineer** | auth, הרשאות, RBAC, sessions, סודות, CORS |

### מכוונים ובודקים (לא כותבים קוד ייצור)
| סוכן | אחריות |
|---|---|
| **product-manager** | מגדיר לכל גרסה: מטרה, scope, **non-goals**, קריטריוני קבלה, חלוקת עבודה |
| **uiux-designer** | מפרטי מסכים, היררכיה, מצבים, נגישות, RTL — ל-frontend-engineer לממש |
| **qa-engineer** | `tests/`, אימות שחרור. כותב בדיקות, לא מתקן קוד ייצור |
| **tax-domain-expert** | ⚠️ הסמכות היחידה לכלל מס. מאשר או **חוסם** לפני מימוש |

### שחרור
| סוכן | אחריות |
|---|---|
| **release-manager** | **הסוכן היחיד** שרשאי למזג ל-`main`, לשנות `VERSION`, וליצור tag |

---

## מודל הברנצ'ים

```
main
 └── release/v0.4.0            ← branch אינטגרציה
      ├── feat/v0.4.0/database      ← ממוזג ראשון (סכמה)
      ├── feat/v0.4.0/backend
      ├── feat/v0.4.0/security
      ├── feat/v0.4.0/frontend
      ├── feat/v0.4.0/integrations
      ├── feat/v0.4.0/reporting
      └── feat/v0.4.0/qa            ← ממוזג אחרון (בודק את הכל)
```

## מחזור חיים של גרסה

1. **product-manager** כותב את מפרט הגרסה מתוך `FEATURE_SPEC.md` → **אתה מאשר**.
2. אם יש כלל מס: **tax-domain-expert** מאשר או חוסם. חסום = לא מתחילים.
3. **uiux-designer** מוציא מפרטי מסכים (אם יש UI בגרסה).
4. המתכנתים עובדים במקביל, כל אחד ב-branch שלו.
5. **release-manager** ממזג לפי סדר ל-`release/<version>`.
6. **qa-engineer** מריץ את כל הסוויטה על branch האינטגרציה.
7. **change-reviewer** (קיים) סוקר את ה-diff המשולב.
8. **release-manager** מבצע bump ל-`VERSION`, ממזג ל-`main`, ומתייג.

---

## ⚠️ הסיכון האמיתי במודל הזה — קראו לפני שמתחילים

הקוד הזה **מונוליטי בדיוק במקומות הלא נכונים**: `routes.py`, `models.py`, `schemas.py`
ושלושת קבצי ה-HTML נוגעים כמעט בכל פיצ'ר. המשמעות: סוכנים שעובדים במקביל **יתנגשו**
באותם קבצים.

**איך מנהלים את זה:**
- `product-manager` קובע **סדר** מפורש כששני סוכנים נוגעים באותו קובץ — לא מניח מיזוג נקי.
- `database-engineer` תמיד ראשון (כולם תלויים בסכמה).
- אינטגרציה מוקדמת ותכופה — לא לתת ל-branch להתרחק גרסה שלמה.
- כמה שיותר לוגיקה עוברת ל-`services/` (קבצים נפרדים) במקום להצטבר ב-`routes.py`.

זו לא סיבה לא לעבוד ככה — זו סיבה לתכנן סדר לפני שמתחילים.

---

## חוקים שחלים על כל הסוכנים

1. **קודם לחפש פתרון חינמי קיים** (GitHub/PyPI/npm), רק אחר כך לכתוב. רישיון מתירני
   בלבד (MIT/Apache-2.0/BSD). לא נמצא → `builder`.
2. **לא ממציאים כלל מס.** אף פעם. → `tax-domain-expert`.
3. **לא מחלישים ולא מוחקים בדיקה** כדי שבנייה תעבור.
4. **לא נוגעים ב-`esop_database.db`** — דאטה עובד, לא fixture.
5. **רק release-manager** נוגע ב-`VERSION`, ב-`main` ובתגיות.
6. **הבאגים המכוונים נשארים** (`qa_bug_accounts.md`) — לא "מתקנים" אותם בלי אישור מפורש.
7. הערות בעברית שמסבירות **למה** invariant קיים — לא מה שהקוד כבר אומר.

---

## יחס לסוכנים הקיימים

בפרויקט כבר היו 6 סוכנים **יועצים בלבד** (קריאה בלבד, לא כותבים קוד):
`product-expert`, `architecture-expert`, `design-expert`, `qa-expert`, `security-expert`,
`change-reviewer`.

הם **לא הוחלפו** — הם משמשים כשכבת ייעוץ/סקירה לפני ואחרי, בעוד שהחדשים הם המבצעים.
במיוחד `change-reviewer` נשאר חלק מחובה מרשימת התיוג של השחרור.

חפיפה מכוונת (יועץ ↔ מבצע):
`product-expert` ↔ `product-manager` · `design-expert` ↔ `uiux-designer` ·
`qa-expert` ↔ `qa-engineer` · `security-expert` ↔ `security-engineer` ·
`architecture-expert` ↔ `backend-engineer` + `database-engineer`.
