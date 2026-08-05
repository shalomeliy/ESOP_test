# MARKET_ANALYSIS — מערכות תגמול הוני בשוק, הכשלים שלהן, והפער שאנחנו נכנסים אליו

מסמך ראיות. הוא הבסיס ל-`GOAL.md` — כל טענה כאן ניתנת למקור (ראו סוף המסמך).
נכתב באוגוסט 2026. **המסמך הזה מתיישן**: מחירים, רגולציה ותכונות משתנים.
לפני שמסתמכים על סעיף מסוים לצורך החלטת מימוש — לאמת מחדש.

---

## 1. מפת השוק — מי השחקנים ומה כל אחד באמת פותר

השוק מפוצל לפי **שלב חיים** ולפי **גיאוגרפיה**, ולא לפי איכות. אין שחקן שלם.

| שכבה | שחקנים מרכזיים | מה הם באמת פותרים | הגבול שלהם |
|---|---|---|---|
| סטארטאפים מגובי VC (ארה"ב) | **Carta**, **Pulley**, **Eqvista**, Cake Equity | טבלת הון + הערכות 409A מצורפות + מנהלי קרנות | תמיכה חלשה בסכמות לא-אמריקאיות (EMI, CSOP, VSOP, BSPCE), Pulley עדיין US-centric |
| אירופה / בריטניה | **Ledgy**, Optio, Capdesk | סכמות אירופאיות, IFRS 2, ריבוי מטבעות, GDPR | **לא מספק 409A** — כלומר לא רלוונטי לחברה עם זרוע אמריקאית |
| ארגונים גדולים / לקראת הנפקה | **Morgan Stanley at Work (Shareworks)**, **J.P. Morgan Workplace Solutions (Global Shares)**, **Certent** | היקף גלובלי, ציבורי+פרטי, תפעול כבד | מסורבל, עקומת לימוד תלולה, מחיר לא מפורסם, ביקורות על UI וסרוויס |
| אסיה / הודו | **Qapita**, EquityList | ESOP מקומי + ציות חוצה-גבולות (Qapita מצהיר על 150+ תחומי שיפוט) | ליבה גיאוגרפית שונה |
| ישראל — נאמנות 102 | **ESOP-Excellence**, **IBI Trust**, **Phoenix ESOP**, בנק הפועלים, לאומי טראסט, **Altshare** | תפקיד הנאמן מול רשות המסים | הנאמן הוא **שירות**, לא מוצר תוכנה שהחברה והעובד עובדים בו |
| נזילות | **Nasdaq Private Market**, **Hiive**, EquityBee, ESO Fund | הצעות רכש, שוק משני, מימון מימוש | מנותק ממערכת הרשומות של החברה |

**המסקנה החשובה ביותר מהמפה:** הפיצול הזה הוא לא היסטוריה — הוא **מודל העסקים**. כל שחקן
בנוי סביב לקוח אחד ומכר תכונות סביבו. לכן חברה ישראלית עם עובדים בארה"ב ובאירופה
מפעילה היום *שלושה* פתרונות במקביל (פלטפורמת הון + נאמן 102 + גיליון Excel לתפר ביניהם),
וההשוואה ביניהם נעשית ידנית. שם נמצא הפער.

---

## 2. הכשלים — 12 בעיות, מה שורש כל אחת, ואיך פותרים אותה

לכל בעיה: **מה קורה בפועל → למה זה קורה → הפתרון ההנדסי**.

### 2.1 טבלת הון שבורה — הכשל הבסיסי ביותר

**מה קורה:** שגיאות לוחות הבשלה הן הנפוצות ביותר. גיליונות אלקטרוניים לא אוכפים
שלמות נתונים, לא מחשבים waterfall אוטומטית, ומייצרים בעיית גרסאות — לחברה יש שלוש
טבלאות "עדכניות" שונות. במקרה מתועד אחד עלות השגיאות (משפטנים, שווי נמוך יותר, ושבעה
שבועות עיכוב בגיוס) עברה **1.2 מיליון דולר**. Tibco שגתה ב-100 מיליון דולר.

**למה:** המצב מוחזק כ**סטייט נוכחי הניתן לעריכה** ולא כ**רצף אירועים**. מי שיכול לערוך
מספר בטבלה, יערוך אותו — ואז אין דרך לדעת מה היה נכון לפני שבוע.

**הפתרון:** ledger מבוסס-אירועים, append-only. המצב הוא תמיד *תוצר חישוב* מרצף
האירועים, לא שדה שמישהו כתב אליו. תיקון הוא אירוע מתקן, לא UPDATE. Invariants
(כמו `allocated + unallocated = total` שכבר קיים אצלנו) נאכפים בשכבת ה-DB, לא בקוד
האפליקציה — כי קוד אפשר לעקוף.

### 2.2 ASC 718 / IFRS 2 — מתגלה בביקורת הראשונה, מאוחר מדי

**מה קורה:** סטארטאפים מגלים את בעיות ASC 718 בביקורת הראשונה — התאמות, עיכובים,
ובדיקה מוגברת מהמשקיעים. הנטייה היא לחכות לסוף השנה ואז לחשב הכל בלחץ. מבקרים
מחפשים במיוחד **שגיאות במועד ההענקה, שמתגלגלות ומצטברות על כל התקופות שאחריו**.
תיעוד חסר של המתודולוגיה הוא כשל בפני עצמו.

**למה:** ההוצאה מחושבת **בדיעבד ובאצווה**, ומהנתונים כפי שהם *עכשיו* — לא כפי שהיו
בכל תקופה. אין ארטיפקט מוקפא לכל תקופה.

**הפתרון:** צבירה רציפה. כל תקופת דיווח מחושבת, **מוקפאת ונחתמת** עם כל התשומות
שלה (הנחות, מודל, שווי, גרסת כלל). מסמך המתודולוגיה נוצר מהנתונים, לא נכתב ביד.
ותיקון תקופה סגורה הוא אירוע מתועד — לא חישוב מחדש שקט.

### 2.3 הערכת שווי מיושנת (409A) — סיכון מס אמיתי, נמנע לחלוטין

**מה קורה:** דגלים אדומים נפוצים: FMV מעל 12 חודשים, או הערכה שמתעלמת מגיוס חדש.
תרחיש קלאסי: חברה מגייסת Series A בינואר, מקבלת 409A חדש רק ביוני, וממשיכה להעניק
אופציות ב-FMV מיושן בין לבין. התוצאה עלולה להיות הכרה מיידית בהכנסה לעובד + **20%
מס ענישה** + ריבית.

**למה:** ההערכה היא **מסמך מצורף**, לא אובייקט עם חלון תוקף שהמערכת אוכפת.

**הפתרון:** הערכת שווי היא ישות עם תאריך תוקף, מקור, וטווח תקפות. **המערכת חוסמת
הענקה שנשענת על הערכה שפג תוקפה** — לא מתריעה, חוסמת. אירוע מהותי (גיוס, מיזוג)
מסמן אוטומטית את ההערכה כטעונת רענון.

### 2.4 ניידות עובדים ומיסוי רב-תחומי — הפער הרווחי ביותר בשוק

**מה קורה:** ניידות עובד מייצרת חבות מס, שכר ודיווח **בכמה תחומי שיפוט על אירוע
בודד**. מדינות שונות אף לא משתמשות באותה מתודולוגיה לייחוס ההכנסה. בניגוד לארה"ב
עם גישת שיעור אחיד, ברוב המדינות יש לנכות בשיעור השכר הרגיל של העובד. בריטניה
דורשת דיווח ERS שנתי. בהודו יש שכבת FEMA. חברות רבות **לא יודעות שהן לא מצייתות**,
כי אין להן בכלל מעקב אחרי היכן העובד עבד.

**למה:** המודל מניח "לעובד יש מדינה". בפועל לעובד יש **היסטוריית שהות**, וההכנסה
מתחלקת בין תחומי שיפוט לפי תקופת ההבשלה.

**הפתרון:** היסטוריית שהות/העסקה כישות ראשונה במעלה, ומנוע ייחוס שמחלק כל אירוע מס
בין תחומי שיפוט לפי ימי עבודה בתקופה הרלוונטית. כל תחום שיפוט הוא **חבילת כללים**
נפרדת עם תאריכי תוקף — לא `if` בקוד.

### 2.5 הכללים משתנים מתחת לרגליים

**מה קורה:** בישראל תוקנו כללי 102 — דוחות שנתיים ורבעוניים גם למסלול נאמן וגם ללא
נאמן, שנתי עד 30 באפריל (ל-2024 נדחה ל-1 באוקטובר 2025), רבעוני תוך 120 יום מסוף
הרבעון שבו בוצעה הענקה. בארה"ב ה-SEC שוקלת מחדש גילוי תגמול בכירים עם הצעת כלל
צפויה באביב/קיץ 2026, ו-FASB ASU 2024-03 תשנה את פירוק ההוצאות בדוח.

**למה:** הכלל **מקודד בתוך הקוד**. שינוי רגולטורי = release.

**הפתרון:** כלל = **דאטה מגורסת עם תאריך תוקף ומקור מצוטט**. חישוב שאין לו כלל
מצוטט — נכשל, לא מנחש. שינוי רגולציה הוא הוספת רשומה, לא שינוי לוגיקה. זה גם מה
שמאפשר לחשב **אחורה** לפי הכלל שהיה בתוקף אז.

### 2.6 העובד לא מבין, ומאבד כסף אמיתי

**מה קורה:** **כמחצית מהאופציות שהיו "בתוך הכסף" ופגו ב-2022 לא מומשו.** 39% מהמחזיקים
אמרו שהם יודעים "מעט" או "כלום" על האופציות שלהם, ועוד 35% "משהו". מעל מחצית מדווחים
שההחלטות מלחיצות, ומעל שני שלישים חושבים שהחברה צריכה לעזור להם להבין. חלון המימוש
הסטנדרטי לאחר עזיבה הוא 90 יום — ועובדים מפספסים תאריך שהם לא ידעו שצריך לעקוב אחריו.

**למה:** הפורטלים בנויים כ**דוח מצב** ("יש לך 4,000 אופציות במחיר מימוש 2.30$"). זה
מידע נכון שלא עונה על שאלת העובד, שהיא: *כמה כסף, מתי, מה זה יעלה לי, ומה קורה אם לא.*

**הפתרון:** צד העובד הוא **מנוע החלטה**, לא דוח. לכל דדליין: הסכום בסיכון בכסף,
עלות המימוש **בתוספת חשבון המס** לפי המסלול הספציפי שלו, ותרחישי מכירה. תזכורות
יזומות עם הסלמה לפי קרבה לדדליין ולפי הסכום שבסיכון. הכשל של אי-מימוש הוא כשל
של המערכת, לא של העובד.

### 2.7 אמון וניגוד עניינים של הספק

**מה קורה:** ב-2024 Carta הואשמה על ידי לקוח בפנייה למשקיעים שלו כדי לתווך מכירת
מניות **בלי הסכמה**; המהלך נעשה ויראלי, לקוחות איימו לעזוב, והחברה בסופו של דבר
הודיעה על יציאה מעסקי המשני. בנפרד, מייסדים התלוננו שביטול מנוי דורש פגישת
"בקשת ביטול" עם מנהל הצלחת לקוח, כשמתחרות מאפשרות ביטול בתוכנה או במייל.

**למה:** הפלטפורמה יושבת על הנתונים הרגישים ביותר בשוק הפרטי, ובמקביל יש לה תמריץ
למונטז אותם.

**הפתרון:** גבול מטרה מוצהר וניתן לאכיפה — נתוני לקוח משמשים ללקוח בלבד. כל שימוש
מעבר לזה דורש הסכמה מפורשת, מתועדת וניתנת לביטול. ביטול וייצוא מלא הם **פעולה
בתוכנה**, ללא שיחה עם אדם.

### 2.8 נעילת ספק ואי-ניידות נתונים

**מה קורה:** פלטפורמות שמגבילות גישה לנתונים או גובות תשלום פרימיום על ייצוא יוצרות
נעילה. מיגרציה בפועל = איסוף קבצים, ייבוא, התאמה, בדיקות, והרצה במקביל עד ש**המספרים
מוכחים כזהים** בין המערכות.

**למה:** הנעילה היא תכונה מכוונת של המודל העסקי.

**הפתרון:** ייצוא מלא — כולל **יומן האירועים המלא**, לא רק תמונת מצב — הוא תכונה
מתועדת בליבה. סכמת ייבוא/ייצוא פומבית. ודוח התאמה ("הוכח שהמספרים זהים") הוא כלי
מוצר, כדי שגם *הכניסה* אלינו תהיה זולה.

### 2.9 היעדר אינטראופרביליות

**מה קורה:** אין APIs סטנדרטיים בתחום, מה שמקשה על סקייל של אינטגרציה. קונים מעדיפים
כיום פתרונות שמתחברים ל-Workday/ADP/Paylocity. תגמול הוני חוצה Finance, HR, Legal,
Tax ו-Payroll — **בלי זרימת נתונים משולבת**, וכשההחלטה במעלה הזרם לא משתקפת במורד
הזרם, נוצר סיכון ציות.

**למה:** האינטגרציה נמכרת כמודול, ולכן נבנתה כתוספת.

**הפתרון:** API-first ו-webhooks בליבה. סנכרון HR/שכר אידמפוטנטי עם דשבורד שגיאות —
כי הפער התפעולי הנפוץ ביותר הוא עזיבה שנרשמה במערכת השכר ולא במערכת ההון.

### 2.10 חוויית שימוש, שקיפות מחיר ועלות

**מה קורה:** Shareworks מתואר כמסורבל וסובל מבלבול מיתוגי (אפליקציה בשם אחד, ווב
בשם אחר), עם ביקורות על ממשק מבלבל ותמיכה שלא אכפת לה, ובעלות של פי 4 מ-Certent
"בלי שיפור משמעותי". Certent עצמו ננטש על ידי מבקרים בגלל ממשק ויכולות. Carta,
J.P. Morgan, Certent ו-Shareworks **לא מפרסמים מחיר** ודורשים שיחת מכירה; Pulley
ו-Cake כן מפרסמים.

**למה:** לקוח ארגוני נעול לא נוטש, ולכן אין תמריץ לשפר.

**הפתרון:** מחיר מפורסם, ממשק לפי תפקיד (מנהל / עובד / נאמן / רו"ח רואה-בלבד),
וקליטה מהירה. אין שער מכירות לפני שרואים את המוצר.

### 2.11 חורים גיאוגרפיים בסוגי המכשירים

**מה קורה:** לפלטפורמות אמריקאיות תמיכה מוגבלת ב-EMI, CSOP, VSOP גרמני, BSPCE צרפתי
ובדרישות GDPR לנתוני עובדים; Ledgy נבנה בדיוק למבנים האלה אבל **לא מספק 409A**.
אף אחד לא שלם בשני הכיוונים.

**למה:** סוג המכשיר מקודד קשה בליבה, אז כל גיאוגרפיה חדשה היא פרויקט.

**הפתרון:** מודל מכשיר **מונע-דאטה**: מכשיר = הגדרה (מה מוענק, מתי נוצר אירוע מס,
מה נדרש לדווח) ולא מחלקה בקוד. מסלול מס = חבילת כללים. כך IL 102 (הון/עבודה),
US ISO/NSO, ו-EMI/BSPCE/VSOP חיים באותו מנוע.

### 2.12 נזילות מנותקת ממערכת הרשומות

**מה קורה:** הצעות רכש הפכו מאירוע אד-הוק לתכונה קבועה בתגמול. לפי נתוני Hiive
היו כ-1,400 מנפיקים פעילים בשוק הפרטי במחצית 2026, מתוכם כ-110 הריצו הצעת רכש
בחסות דירקטוריון בשנה שקדמה — פי שלושה מאז 2021. הסיבה: **אקוויטי בלי מסלול נזילות
נראה-לעין לא מחזיק עובדים.**

**למה:** נזילות היא תעשייה נפרדת (NPM, Hiive), אז היא לא יושבת על אותו ledger.

**הפתרון:** הצעת רכש כזרימה על אותו ledger — זכאות, קישור ל-FMV/409A, חישוב ניכוי
במקור לפי מסלול המס של כל משתתף, ורישום ב-audit. הנתון קיים כבר במערכת; אין סיבה
לייצא אותו לאקסל.

### 2.13 בונוס: הוואקום סביב AI

**מה קורה:** לפי סקירת התחום ל-2026, בעלי המקצוע בוחנים הטמעת AI לאורך מחזור החיים
של תגמול הוני **אבל חסרות מסגרות governance** — אין תקנים מוסכמים, ולא ברור אילו
תהליכים בכלל צריכים אוטומציה מול שיפור אנליטי.

**הפתרון (ובמידה רבה יתרון תחרותי):** AI **מחוץ למסלול המספרים**. המנוע דטרמיניסטי
ומאומת; AI מסביר, מנסח, מתריע ומכין טיוטות — ולעולם לא מייצר סכום שנכנס לדוח מס.
כל פלט AI מסומן ככזה וניתן למעקב לתשומות שלו. זה בדיוק הגבול שהתעשייה עדיין לא
העמידה.

---

## 3. הפער שאף אחד לא ממלא — ההזדמנות בשורה אחת

> אין מערכת אחת שהיא **גם** מדויקת עמוקות בכל תחום שיפוט **וגם** ניתנת להוכחה לאחור
> על כל מספר **וגם** מובנת לעובד שצריך להחליט **וגם** לא נועלת את הלקוח.

שבעה דברים שאיש אינו עושה היום היטב, וכל אחד מהם ניתן להנדסה:

1. **אמת נקודתית-בזמן (bitemporal)** — לא רק "מה נכון עכשיו" אלא "מה **האמנו** שנכון
   בתאריך X, ולפי איזה כלל וכיזו הערכה". זה מה שהופך ביקורת מחקירה לשליפה.
2. **חישוב כארטיפקט שאפשר להגן עליו** — כל מספר נושא איתו: תשומות, גרסת כלל + מקור,
   הערכת שווי, וגרסת קוד. שחזור מדויק בכל רגע בעתיד.
3. **ציות חוסם ולא מדווח** — המערכת מסרבת ליצור מענק לא כשיר (הענקה לפני 30 יום
   מהגשת התוכנית לרשות המסים, FMV שפג תוקפו) במקום לדווח על זה ברבעון הבא.
4. **הנאמן כצד ראשון במעלה** — בישראל הנאמן הוא חלק מהחוקה של המסלול, לא ספק חיצוני.
   פלטפורמות גלובליות מתייחסות אליו כהערת שוליים.
5. **תמיכת החלטה לעובד** ברמת המסלול הספציפי שלו, בכסף — לא הצגת נתונים.
6. **ניידות נתונים כתכונה** — יומן אירועים מלא, סכמה פומבית, דוח התאמה.
7. **דטרמיניזם מוכח בבדיקות** — היכולת להראות שאותו קלט מייצר אותו פלט היא לא היגיינה
   הנדסית בלבד; בדומיין הזה היא **טיעון מכירה מול מבקר**.

---

## 4. מה זה אומר לארכיטקטורה שלנו — עשר החלטות שנגזרות מהניתוח

| # | החלטה | הבעיה שהיא סוגרת |
|---|---|---|
| 1 | Event-sourced ledger, append-only; מצב = תוצר חישוב | 2.1 |
| 2 | Bitemporal: תאריך אפקטיבי + תאריך ידיעה, על כל ישות | 2.1, 2.2, 3.1 |
| 3 | כלל מס = דאטה מגורסת עם תאריך תוקף ומקור מצוטט; אין כלל → נכשל | 2.5, 2.11 |
| 4 | Invariants ב-DB, לא בקוד אפליקציה | 2.1 |
| 5 | הערכת שווי עם חלון תוקף שחוסם הענקה | 2.3 |
| 6 | היסטוריית שהות + מנוע ייחוס בין תחומי שיפוט | 2.4 |
| 7 | מודל מכשיר ומסלול מונע-דאטה, לא מחלקות בקוד | 2.11 |
| 8 | API-first + webhooks + סנכרון HR אידמפוטנטי בליבה | 2.9 |
| 9 | ייצוא מלא של יומן האירועים + דוח התאמה, כתכונה | 2.8, 2.7 |
| 10 | AI רק בשכבת הסבר/טיוטה; אסור במסלול שמייצר מספר | 2.13 |

---

## 5. מקורות

טבלת הון ושלמות נתונים — [Carta: Broken cap tables](https://carta.com/blog/broken-cap-tables/) ·
[Pulley: 7 cap table mistakes](https://pulley.com/blog-posts/7-cap-table-mistakes-and-how-to-avoid-them) ·
[NASPP: Avoid 5 critical cap table mistakes](https://www.naspp.com/blog/avoid-5-critical-cap-table-management-mistakes) ·
[Investor Ready: The broken cap table audit](https://investorreadycapital.com/news-insights/the-broken-cap-table-audit-what-happens-when-your-equity-records-dont-match-your-cap-table-software)

ASC 718 — [Eqvista: common ASC 718 mistakes](https://eqvista.medium.com/common-mistakes-startups-make-when-implementing-asc-718-2cee452bb36b) ·
[Eqvista: ASC 718 best practices](https://eqvista.com/tax-guides-compliance/asc-718/asc-718-reporting/) ·
[Burkland: ASC 718 compliance](https://burklandassociates.com/compliance-hub/asc-718-compliance/)

409A / FMV — [Eqvista: 409A guide 2026](https://eqvista.com/409a-valuation/complete-guide-for-409a-valuation/) ·
[Mantle: 409A valuations](https://blog.withmantle.com/409a-valuations/) ·
[Morgan Stanley at Work: 409A FAQ](https://www.morganstanley.com/atwork/articles/409a-valuation-faq)

ניידות ומיסוי רב-תחומי — [GTN: mobile employee equity income](https://www.gtn.com/blog/considerations-for-mobile-employee-equity-income-article-gtn) ·
[NASPP: global stock plan tax withholding](https://www.naspp.com/blog/Tips-for-Global-Stock-Plan-Tax-Withholding) ·
[Infinite Equity: global tax withholding](https://infiniteequity.com/global-design-administration/global-tax-withholding-for-employee-equity-awards/) ·
[The Tax Adviser: revisiting withholding](https://www.thetaxadviser.com/issues/2021/apr/revisiting-withholding-equity-compensation/) ·
[Legal500: cross-border ESOPs in India (FEMA)](https://www.legal500.com/developments/thought-leadership/cross-border-esops-in-india-legal-tax-and-fema-considerations-for-multinational-companies-gccs-and-global-workforces/)

ישראל / סעיף 102 — [NASPP: Israel Section 102 pitfalls](https://www.naspp.com/blog/israel-section-102--equity-plan-pitfalls-to-avoid) ·
[NASPP: hiring in Israel](https://www.naspp.com/blog/hiring-in-israel--how-section-102-shapes-equity-compensation) ·
[Herzog: new and amended 102 rules](https://herzoglaw.co.il/en/news-and-insights/new-and-amended-102-rules/) ·
[Slice: Section 102 reporting deadlines](https://www.sliceglobal.com/blog-posts/section-102-israel-reporting-2025) ·
[BDO: Israel ESOP reporting amended](https://www.bdo.global/en-gb/insights/tax/expatriate-tax/israel-esop-reporting-regulations-amended) ·
[Phoenix ESOP: trustee services](https://www.xnes.co.il/en/esop/)

הבנת העובד ואיבוד אופציות — [Carta: 2022 employee stock options report](https://carta.com/data/2022-employee-stock-options-report/) ·
[Carta: why employees don't exercise](https://carta.com/blog/why-employees-dont-exercise-stock-optionsand-what-companies-can-do-to-help/) ·
[Wharton: how employees value their options](https://knowledge.wharton.upenn.edu/podcast/knowledge-at-wharton-podcast/how-employees-value-often-incorrectly-their-stock-options/) ·
[EquityBee: leaving and your options](https://equitybee.com/post/why-your-stock-options-matter-if-youre-leaving)

אמון ספק — [TechCrunch: Carta accused of unethical tactics](https://techcrunch.com/2024/01/07/carta-the-cap-table-management-outfit-is-accused-of-unethical-tactics-by-a-customer-after-it-tries-broker-a-deal-for-a-startups-shares-without-consent) ·
[Capital Brief: trading shares without consent](https://www.capitalbrief.com/briefing/carta-customers-accuse-platform-of-trading-shares-without-consent-eaad03b7-b9b1-40e1-aaf7-23002f932427/)

נעילה, מיגרציה, אינטגרציה — [Morgan Stanley: switching equity plan providers](https://www.morganstanley.com/atwork/articles/switching-equity-plan-providers) ·
[J.P. Morgan: 10 questions before switching](https://www.jpmorganworkplacesolutions.com/insights/switch-equity-management-software/) ·
[Shareforce: share plan software integrations](https://www.shareforce.net/blog/8-share-plan-software-integrations-for-hr-and-finance/)

השוואת פלטפורמות ומחיר — [Pulley: Carta competitors](https://pulley.com/guides/carta-competitors) ·
[Cake Equity: best equity management software](https://www.cakeequity.com/guides/best-equity-management-software) ·
[Ledgy: equity plan automation](https://ledgy.com/equity-plan-automation) ·
[Qapita: ASC 718 / financial reporting](https://www.qapita.com/equity-management/financial-reporting) ·
[Capterra: Shareworks reviews](https://www.capterra.com/p/166953/Shareworks/reviews/) ·
[G2: Shareworks reviews](https://www.g2.com/products/shareworks-by-morgan-stanley/reviews)

מגמות 2026 ו-AI — [Equity Methods: what's ahead in stock compensation for 2026](https://www.equitymethods.com/articles/our-take-on-whats-ahead-in-stock-compensation-for-2026/)

נזילות — [Carlton Fields: structuring an employee tender offer](https://www.carltonfields.com/insights/publications/2026/structuring-an-employee-tender-offer-program-key-considerations-for-private-companies) ·
[Nasdaq Private Market](https://www.nasdaqprivatemarket.com/nasdaq-private-market-announces-first-ever-employee-tender/) ·
[CT Acquisitions: private company tender offer 2026](https://ctacquisitions.com/private-company-tender-offer/)
