/* מסמכים ואישור קבלה - מימוש אחד לשלושת הפורטלים (v0.9.0 שלב 3).
 *
 * *** אישור קבלה, לא חתימה ***: למערכת אין אימות זהות, אין הצפנה ואין גורם
 * שלישי מאשר. המילים "חתימה"/signature/signed לא מופיעות בקובץ הזה בכוונה,
 * בדיוק כמו בשכבת ה-API ובגוף ה-PDF - מונח שמרמז על תוקף משפטי שאין לו הוא
 * אותה הפרה כמו כלל מס בדוי.
 *
 * למה קובץ משותף ולא קוד בכל פורטל: תוויות הסטטוס וההורדה המאומתת זהות
 * בשלושת הפורטלים. שלושה עותקים היו נסחפים זה מזה בגרסה הראשונה שתוסיף
 * סטטוס - וזה בדיוק דפוס P3 (התנהגות שקיימת בנתיב אחד וחסרה בשני).
 */
(function (global) {
  "use strict";

  var STATUS = {
    DRAFT: { label: "טיוטה", cls: "bg-slate-500/10 text-slate-400" },
    SENT: { label: "נשלח לאישור", cls: "bg-amber-500/10 text-amber-400" },
    ACKNOWLEDGED: { label: "אושרה קבלה", cls: "bg-emerald-500/10 text-emerald-400" },
    DECLINED: { label: "נדחה", cls: "bg-rose-500/10 text-rose-400" },
    // EXPIRED מוגדר במכונת המצבים אבל שום קוד לא מייצר אותו - אין scheduler
    // ואין מדיניות תפוגה. לכן: תווית בלבד, בלי ספירת ימים לתפוגה בשום מסך.
    // ספירה כזו הייתה מרמזת על דיוק שלא קיים (docs/qa/v0.9.0.md סיכון 8).
    EXPIRED: { label: "פג תוקף", cls: "bg-slate-600/10 text-slate-500" },
  };

  var TEMPLATES = {
    GRANT_LETTER: "כתב הענקה",
    SECTION_102_APPENDIX: "נספח 102 (תבנית דמו)",
    TRUSTEE_DEPOSIT_CONFIRMATION: "אישור הפקדה בנאמנות",
  };

  function statusBadge(status) {
    var s = STATUS[status] || { label: status, cls: "bg-slate-500/10 text-slate-400" };
    return '<span class="text-xs px-2 py-0.5 rounded ' + s.cls + '">' + s.label + "</span>";
  }

  function templateLabel(type) {
    return TEMPLATES[type] || type;
  }

  // כל טקסט שמגיע מה-DB עובר כאן לפני שהוא נכנס ל-innerHTML. שם עובד הוא טקסט
  // חופשי שאדמין מקליד, והוא מוצג גם בפורטל *הנאמן* - כלומר קלט של גורם אחד
  // מורץ בסשן של גורם אחר. הבריחה יושבת ב-orDash כי זו נקודת המעבר היחידה של
  // כל השדות הטקסטואליים בשלוש הטבלאות.
  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // שדה שחזר null הוא נתון חסר, לא ערך עסקי - מוצג כמקף ולא כתא ריק שנראה
  // תקין (QA_TESTBOOK.md P4, אותה סיבה שהשרת מחזיר null ולא "").
  function orDash(value) {
    return (value === null || value === undefined || value === "") ? "—" : escapeHtml(value);
  }

  /* *** נמצא בסקירת שלב 3 ***: השרת כותב datetime.utcnow() לעמודה naive, ולכן
   * מחזיר "2026-08-08T10:51:13" בלי Z ובלי היסט. JS מפרש מחרוזת date-time ללא
   * היסט כזמן *מקומי* (ES-262), כך שכל חותמת הוצגה בהיסט של אזור הזמן - ובסביבות
   * חצות גם בתאריך שגוי. זה השדה היחיד שכל הפיצ'ר קיים כדי לתעד, ולכן ה-Z
   * מתווסף כאן במפורש. אם אי פעם השרת יתחיל להחזיר היסט משלו, התנאי לא ידרוס
   * אותו (P6 - נאמנות טיפוסים בין כתיבה לקריאה). */
  function parseServerTimestamp(value) {
    var hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
    return new Date(hasZone ? value : value + "Z");
  }

  function formatTimestamp(value) {
    if (!value) return "—";
    var d = parseServerTimestamp(value);
    if (isNaN(d.getTime())) return orDash(value);
    return d.toLocaleDateString("he-IL") + " " + d.toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" });
  }

  /* חילוץ נוסח השגיאה מתשובת השרת. JSON.parse על גוף שאינו JSON (500 בטקסט
   * חופשי, HTML מ-proxy) זורק בעצמו ומחליף את השגיאה האמיתית ב-"Unexpected
   * token <" - ולכן הפענוח עטוף, והגוף הגולמי הוא ברירת המחדל. */
  async function errorDetail(res) {
    var body = await res.text();
    try {
      return JSON.parse(body).detail || body;
    } catch (e) {
      return body || ("HTTP " + res.status);
    }
  }

  /* הורדה מאומתת. תגית <a href> לא נושאת את כותרת ה-Authorization ולכן הייתה
   * מקבלת 401 - הקובץ נמשך ב-fetch ומוגש לדפדפן כ-blob. ה-endpoint מפעיל את
   * בדיקת הבעלות ורושם שורת audit, ולכן זו הדרך היחידה להגיע ל-PDF; קבצי
   * document_store לעולם לא מוגשים כסטטיים. */
  async function downloadDocument(apiBase, path, headers, fallbackName) {
    var res = await fetch(apiBase + path, { headers: headers });
    if (!res.ok) {
      throw new Error(await errorDetail(res));
    }
    var blob = await res.blob();
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = fallbackName || "document.pdf";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function fileNameFor(doc) {
    return doc.template_type + "_" + doc.grant_id + "_v" + doc.version + ".pdf";
  }

  // סימון גרסה מיושנת. זהה בשלושת הפורטלים בכוונה: השרת דוחה כל מעבר מצב על
  // גרסה כזו (assert_is_current_version), והמסך חייב להסביר את זה *לפני*
  // הלחיצה ולא אחריה.
  function supersededMarker(doc) {
    return doc.is_latest ? "" : '<span class="block text-[10px] text-slate-500">גרסה מיושנת</span>';
  }

  global.ESOPDocuments = {
    statusBadge: statusBadge,
    templateLabel: templateLabel,
    escapeHtml: escapeHtml,
    orDash: orDash,
    formatTimestamp: formatTimestamp,
    errorDetail: errorDetail,
    downloadDocument: downloadDocument,
    fileNameFor: fileNameFor,
    supersededMarker: supersededMarker,
  };
})(window);
