/* ייצוא/ייבוא/התאמה - עוזרי תצוגה משותפים (v0.9.1 שלב ב, PLAN.md §8 step 11).
 *
 * למה קובץ משותף כמו documents.js ולא קוד בתוך index_manage.html: תוויות
 * הכיוון/הסטטוס וההורדה המאומתת הן בדיוק מהסוג שדפוס P3 מזהיר מפניו - קוד
 * שקיים בפורטל אחד וחסר בשני כשיתווסף עוד פורטל שרואה את המסך הזה.
 * המסך עצמו כרגע admin-only (require_roles(COMPANY_ADMIN) בשרת), אבל
 * העוזרים עצמם לא תלויים בכך.
 */
(function (global) {
  "use strict";

  var ESOPDocs = global.ESOPDocuments;

  var DIRECTIONS = {
    EXPORT: { label: "ייצוא", icon: "fa-file-export", cls: "bg-indigo-500/10 text-indigo-400" },
    IMPORT_DRY_RUN: { label: "ייבוא (בדיקה)", icon: "fa-vial", cls: "bg-slate-500/10 text-slate-400" },
    IMPORT_COMMIT: { label: "ייבוא (בוצע)", icon: "fa-file-import", cls: "bg-emerald-500/10 text-emerald-400" },
  };

  // COMMITTED הוא לא "כשל" ולא "בתהליך" - הוא דריי-ראן שכבר נוצל בהצלחה
  // ל-commit (models.py: DataTransferStatus.COMMITTED docstring). תווית
  // נפרדת כדי שלא יתפרש כ-PENDING (שלא קיים בפועל בזרימה הסינכרונית הזו,
  // v0.9.1 אין תור-עבודה) או כ-FAILED.
  var STATUSES = {
    PENDING: { label: "בתהליך", icon: "fa-spinner", cls: "bg-amber-500/10 text-amber-400" },
    SUCCESS: { label: "הצליח", icon: "fa-circle-check", cls: "bg-emerald-500/10 text-emerald-400" },
    FAILED: { label: "נכשל", icon: "fa-circle-xmark", cls: "bg-rose-500/10 text-rose-400" },
    COMMITTED: { label: "נוצל - בוצע ייבוא על בסיסו", icon: "fa-lock", cls: "bg-slate-600/10 text-slate-400" },
  };

  function directionBadge(direction) {
    var d = DIRECTIONS[direction] || { label: direction, icon: "fa-question", cls: "bg-slate-500/10 text-slate-400" };
    return '<span class="text-xs px-2 py-0.5 rounded ' + d.cls + '"><i class="fa-solid ' + d.icon + '"></i> ' + d.label + '</span>';
  }

  function runStatusBadge(status) {
    var s = STATUSES[status] || { label: status, icon: "fa-question", cls: "bg-slate-500/10 text-slate-400" };
    return '<span class="text-xs px-2 py-0.5 rounded ' + s.cls + '"><i class="fa-solid ' + s.icon + '"></i> ' + s.label + '</span>';
  }

  // סיכום שורות קריא - attempted/succeeded/failed, בדיוק השדות שקיימים על
  // DataTransferRunOut (אין breakdown של new/skipped_existing על שורת
  // ההיסטוריה עצמה - זה רק בתגובת ה-dry-run החיה, ראו openRunDetailModal).
  function rowsSummary(run) {
    var text = run.rows_succeeded + "/" + run.rows_attempted + " הצליחו";
    if (run.rows_failed) text += " · " + run.rows_failed + " נכשלו";
    return text;
  }

  // אייקון וטקסט עברי תמיד יחד - לעולם לא צבע בלבד (PLAN.md §5).
  function matchCell(clean, mismatchCount) {
    if (clean) {
      return '<span class="text-emerald-400 text-sm"><i class="fa-solid fa-circle-check"></i> תואם - אין אי-התאמות</span>';
    }
    return '<span class="text-amber-400 text-sm"><i class="fa-solid fa-triangle-exclamation"></i> נמצאו ' + mismatchCount + ' אי-התאמות</span>';
  }

  // source_value/target_value הם Any מכוונת (float/date/str/null, תלוי איזה
  // שדה סטה - ReconciliationMismatchOut ב-schemas.py). null אמיתי (למשל
  // ExerciseRequest חסר ביעד) הוא נתון חסר, לא "0" - ולכן orDash ולא "0".
  function mismatchValue(value) {
    return ESOPDocs.orDash(value);
  }

  global.ESOPExportImport = {
    directionBadge: directionBadge,
    runStatusBadge: runStatusBadge,
    rowsSummary: rowsSummary,
    matchCell: matchCell,
    mismatchValue: mismatchValue,
  };
})(window);
