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
    // מ-v0.9.1 יש מדיניות תפוגה אמיתית (30 יום מהשליחה, expires_at על השורה),
    // ולכן ספירת הימים ב-deadlineMarker מותרת: היא נגזרת מערך מאוחסן ולא
    // ממדיניות משוערת. סיכון 8 ב-docs/qa/v0.9.0.md נסגר.
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

  /* שורת/פסקת שגיאה מוברחת ומוכנה להזרקה. נוספה ב-v1.1.1 (פריט ג) אחרי שנמצאו
   * 11 מקומות שהזריקו err.message *גולמי* ל-innerHTML לצד 9 שכן קראו ל-
   * escapeHtml - שני דפוסים באותו קובץ, כלומר הקורא הבא לא יודע מה הכלל.
   *
   * וההנחה ש"זה בסדר כי הערך מגיע מ-<select>" לא נכונה: err.message הוא *גוף
   * התשובה*, ראו errorDetail ממש למעלה. FastAPI מחזירה בו פרמטרים מהבקשה, ו-500
   * בטקסט חופשי או HTML מ-proxy נכנסים ישר לתוך <td> ומפרקים את הטבלה. זה הפגם
   * החי כאן - לא XSS (אין דרך למשתמש אחר לשלוט בפרמטרים האלה), אבל גם לא תקין.
   *
   * הקיטום יושב כאן ולא בכל אתר קריאה, וזו הסיבה שזה helper אחד ולא 11 קריאות
   * ל-escapeHtml: גוף HTML שלם בתוך תא טבלה הוא פגם גם כשהוא מוברח. */
  var ERROR_MAX_CHARS = 300;

  function errorMessage(message) {
    var text = String(message === null || message === undefined ? "" : message);
    if (text.length > ERROR_MAX_CHARS) text = text.slice(0, ERROR_MAX_CHARS) + "…";
    return escapeHtml(text) || "שגיאה לא מזוהה";
  }

  /* cls הוא פרמטר ולא קבוע כי שלושת הפורטלים לא חלקו קלאס אחד מלכתחילה (admin
   * "p-4", trustee/employee "py-4", ושני אתרים עם text-xs/text-sm משלהם). ברירת
   * מחדל אחת הייתה משנה עיצוב בשקט, וזה לא מה ש-patch של הברחה אמור לעשות.
   *
   * Number ו-escapeHtml על colspan ו-cls: שניהם נכנסים לתוך אטריביוט, שהוא הקשר
   * שונה מתוכן. כל אתרי הקריאה מעבירים ליטרל, ולכן זו הגנה שלא עולה כלום - וכן
   * הדבר שכבר נשבר במקום אחד בקודבייס הזה (ראו openEmployeeModal). */
  function errorRow(colspan, message, cls) {
    return '<tr><td colspan="' + Number(colspan) + '" class="'
      + escapeHtml(cls || "p-4 text-center text-red-400") + '">'
      + errorMessage(message) + "</td></tr>";
  }

  function errorText(message, cls) {
    return '<p class="' + escapeHtml(cls || "text-sm text-red-400 text-center py-4") + '">'
      + errorMessage(message) + "</p>";
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

  /* מועד פקיעת בקשת האישור. מוצג רק על מסמך SENT שיש לו expires_at: מסמך
   * שנשלח לפני v0.9.1 חסר דדליין, ו"נותרו 0 ימים" עליו הוא בדיוק השקר ש-P4
   * מזהיר מפניו - נתון חסר שמוצג כערך עסקי. הספירה בימים שלמים כלפי מעלה,
   * כך ש"נותר יום אחד" נכון עד הרגע האחרון ולא הופך ל-0 בבוקר האחרון. */
  function deadlineMarker(doc) {
    if (doc.status !== "SENT" || !doc.expires_at) return "";
    var deadline = parseServerTimestamp(doc.expires_at);
    if (isNaN(deadline.getTime())) return "";
    var daysLeft = Math.ceil((deadline.getTime() - Date.now()) / 86400000);
    var urgent = daysLeft <= 3;
    return '<span class="block text-[10px] ' + (urgent ? "text-amber-400" : "text-slate-500") + '">'
      + "לאישור עד " + escapeHtml(deadline.toLocaleDateString("he-IL"))
      + (daysLeft > 0 ? " · נותרו " + daysLeft + " ימים" : "")
      + "</span>";
  }

  global.ESOPDocuments = {
    statusBadge: statusBadge,
    deadlineMarker: deadlineMarker,
    templateLabel: templateLabel,
    escapeHtml: escapeHtml,
    orDash: orDash,
    formatTimestamp: formatTimestamp,
    errorDetail: errorDetail,
    errorMessage: errorMessage,
    errorRow: errorRow,
    errorText: errorText,
    downloadDocument: downloadDocument,
    fileNameFor: fileNameFor,
    supersededMarker: supersededMarker,
  };
})(window);
