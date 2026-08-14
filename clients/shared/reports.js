/* דוחות, ייצוא ו-BI (v1.1.0) - עוזרי תצוגה משותפים, אותו דפוס בדיוק כמו
 * export_import.js/documents.js: תוויות/badges/פורמט כאן, קריאות ה-API
 * (session, authHeaders, apiGet) נשארות ב-index_manage.html כמו כל שאר הטאבים.
 *
 * כרגע admin-only (require_roles(COMPANY_ADMIN) בשרת) - הקובץ נמצא ב-shared/
 * ולא בתוך admin_portal/ מאותה סיבה בדיוק שexport_import.js שם: אם עוד פורטל
 * יראה דוח כלשהו בעתיד (למשל v1.5.0/RBAC), אין להתחיל שוב מ-2 מימושים.
 */
(function (global) {
  "use strict";

  var ESOPDocs = global.ESOPDocuments;

  // ===================================================================
  // מטא-דאטה של שבעת סוגי הדוחות - endpoint נגזר אוטומטית מה-key (תואם
  // 1:1 ל-SAVED_REPORT_TYPES/REPORT_TITLES בשרת, backend/app/models.py +
  // services/reports.py - לא רשימה עצמאית שיכולה לסטות).
  // dateRange: "none" (אין מסנן תאריכים) | "optional" | "required" (movement -
  // לשרת אין ברירת מחדל, ראו api/reports.py::report_movement).
  // ===================================================================
  var REPORT_TYPES = [
    { key: "POOL_STATUS", label: "סטטוס פולים", icon: "fa-layer-group", dateRange: "none" },
    { key: "TRUSTEE_EXPOSURE", label: "חשיפה לפי נאמן", icon: "fa-user-shield", dateRange: "none" },
    { key: "DEADLINE_RISK", label: "עובדים בסיכון דדליין", icon: "fa-triangle-exclamation", dateRange: "none" },
    { key: "EXERCISE_ACTIVITY", label: "פעילות מימושים לתקופה", icon: "fa-hand-holding-dollar", dateRange: "optional" },
    { key: "COMPENSATION_EXPENSE", label: "הוצאת תגמול הוני (אומדן)", icon: "fa-flask", dateRange: "none" },
    { key: "MOVEMENT", label: "דוח תנועה תקופתי", icon: "fa-timeline", dateRange: "required" },
    { key: "ASC718_READINESS", label: "מוכנות ASC 718 (checklist)", icon: "fa-list-check", dateRange: "none" },
  ];

  function reportTypeMeta(key) {
    for (var i = 0; i < REPORT_TYPES.length; i++) {
      if (REPORT_TYPES[i].key === key) return REPORT_TYPES[i];
    }
    return { key: key, label: key, icon: "fa-file", dateRange: "none" };
  }

  // "ASC718_READINESS" -> "/admin/reports/asc718-readiness" - נגזר, לא ממופה
  // ביד, כדי שרשימת השרת (models.py::SAVED_REPORT_TYPES) תישאר מקור האמת היחיד.
  function endpointPath(key) {
    return "/admin/reports/" + key.toLowerCase().replace(/_/g, "-");
  }

  function reportTypeOptionsHtml(selected) {
    return REPORT_TYPES.map(function (t) {
      return '<option value="' + t.key + '"' + (t.key === selected ? " selected" : "") + '>' + ESOPDocs.escapeHtml(t.label) + "</option>";
    }).join("");
  }

  function savedReportOptionsHtml(savedReports, selectedId) {
    var options = '<option value="">- דוח חדש (לא שמור) -</option>';
    options += savedReports.map(function (r) {
      var meta = reportTypeMeta(r.report_type);
      var shareTag = r.is_private ? "פרטי" : "משותף לחברה";
      var label = r.name + " · " + meta.label + " · " + shareTag;
      return '<option value="' + r.report_id + '"' + (r.report_id === selectedId ? " selected" : "") + '>' + ESOPDocs.escapeHtml(label) + "</option>";
    }).join("");
    return options;
  }

  // ===================================================================
  // Badges - חוזרים בטבלה, בסיכום ובלגנד של הגרפים
  // ===================================================================

  // כפילות מכוונת מול notifications.js::SEVERITY (dot/text/border classes) -
  // הערכים (info/warning/critical) מגיעים משם (services/notifications.py:40),
  // אבל המפה לא exported, ואותה מוסכמה כבר קיימת בקובץ הזה (DOC_TEMPLATE_TYPES
  // ב-index_manage.html, מתועד שם כ"debt item 2").
  var SEVERITY_META = {
    critical: { dot: "bg-rose-500", text: "text-rose-400", border: "border-rose-500/30", label: "קריטי" },
    warning: { dot: "bg-amber-500", text: "text-amber-400", border: "border-amber-500/30", label: "אזהרה" },
    info: { dot: "bg-sky-500", text: "text-sky-400", border: "border-sky-500/30", label: "מידע" },
  };
  function severityBadge(sev) {
    var m = SEVERITY_META[sev] || { dot: "bg-slate-500", text: "text-slate-400", border: "border-slate-500/30", label: sev };
    return '<span class="inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded border ' + m.text + " " + m.border + '">' +
      '<span class="w-1.5 h-1.5 rounded-full ' + m.dot + '"></span>' + ESOPDocs.escapeHtml(m.label) + "</span>";
  }

  // אותם שלושת הצבעים שכבר בשימוש בטבלת בקשות המימוש עצמה (index_manage.html,
  // עמודת סטטוס) - לא מומצא כאן ניואנס חדש לאותם שלושה סטטוסים.
  var EXERCISE_STATUS_CLS = {
    PENDING: "bg-amber-500/10 text-amber-400",
    APPROVED: "bg-emerald-500/10 text-emerald-400",
    REJECTED: "bg-rose-500/10 text-rose-400",
  };
  function exerciseStatusBadge(status) {
    var cls = EXERCISE_STATUS_CLS[status] || "bg-slate-500/10 text-slate-400";
    return '<span class="text-xs px-2 py-0.5 rounded ' + cls + '">' + ESOPDocs.escapeHtml(status) + "</span>";
  }

  // *** "אומדן" - מוסכמה חדשה (PLAN v1.1.0, "תיוג אומדן") ***
  // שונה במפורש מ"לא זמין" (למטה): ערך שכן ניתן לחישוב ומוצג במלואו, לא ערך
  // שמוסתר. amber, אבל אייקון *שונה* (fa-flask, לא fa-circle-exclamation) -
  // בדיוק כדי שלא יתפרש בטעות כ"לא זמין" למרות שהצבע דומה. אייקון+מילה תמיד
  // ביחד, ולא צבע בלבד.
  function estimateBanner(explanation) {
    return '<div class="flex items-start gap-2 p-3 bg-amber-500/10 border border-amber-500/30 rounded-xl text-amber-300 text-sm">' +
      '<i class="fa-solid fa-flask mt-0.5"></i>' +
      '<div><span class="font-bold">אומדן</span>' +
      '<p class="text-xs text-amber-300/80 mt-0.5">' + ESOPDocs.escapeHtml(explanation) + "</p></div></div>";
  }
  function estimateChip() {
    return '<span class="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20" ' +
      'title="אומדן - ללא מקור שווי הוגן מאושר, מבוסס על המחיר הקודם הקרוב ביותר לתאריך המענק">' +
      '<i class="fa-solid fa-flask"></i> אומדן</span>';
  }

  // "לא זמין" - המוסכמה הקיימת (index_manage.html~1954, banner בטבלת ההון):
  // ערך שאי-אפשר לחשב בכלל (fa-circle-exclamation). *לא* אומדן - למשל
  // trustee-exposure.estimated_value שמור לv1.4.0 ותמיד null, לא מחיר-משוער.
  function notAvailableInline(title) {
    return '<span class="text-amber-400 text-xs" title="' + ESOPDocs.escapeHtml(title) + '">' +
      '<i class="fa-solid fa-circle-exclamation"></i> לא זמין</span>';
  }
  function notAvailableBanner(title, text) {
    return '<div class="flex items-start gap-2 p-3 bg-amber-500/10 border border-amber-500/30 rounded-xl text-amber-300 text-sm">' +
      '<i class="fa-solid fa-circle-exclamation mt-0.5"></i>' +
      '<div><span class="font-bold">' + ESOPDocs.escapeHtml(title) + "</span>" +
      (text ? '<p class="text-xs text-amber-300/80 mt-0.5">' + ESOPDocs.escapeHtml(text) + "</p>" : "") + "</div></div>";
  }

  // שלוש סיבות "לא זמין" *נבדלות* (PLAN v1.1.0 #5) - לא מתמוססות לבאדג' אחד
  // גנרי. null (לא הוחרג) מוצג כ"נכלל בחישוב", לא כתא ריק.
  var EXCLUSION_REASON_LABELS = {
    NO_PRICE_DATA: "אין שום נתון מחיר לחברה זו",
    NO_PRECEDING_PRICE: "אין מחיר קודם לתאריך המענק",
    CURRENCY_MISMATCH: "חוסר התאמת מטבע (מענק/מחיר)",
  };
  var EXCLUSION_REASON_ORDER = ["NO_PRICE_DATA", "NO_PRECEDING_PRICE", "CURRENCY_MISMATCH"];
  function exclusionReasonCell(reason) {
    if (!reason) {
      return '<span class="text-xs px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400">נכלל בחישוב</span>';
    }
    var label = EXCLUSION_REASON_LABELS[reason] || reason;
    return '<span class="text-xs px-2 py-0.5 rounded bg-rose-500/10 text-rose-400">' + ESOPDocs.escapeHtml(label) + "</span>";
  }

  function boolBadge(value) {
    if (value === true) return '<span class="text-emerald-400 text-xs"><i class="fa-solid fa-circle-check"></i> כן</span>';
    if (value === false) return '<span class="text-slate-500 text-xs"><i class="fa-solid fa-circle-xmark"></i> לא</span>';
    return ESOPDocs.orDash(value);
  }

  function numberCell(value) {
    if (value === null || value === undefined) return ESOPDocs.orDash(value);
    return Number(value).toLocaleString("he-IL", { maximumFractionDigits: 2 });
  }

  // ===================================================================
  // טבלה גנרית - columns/rows הם ReportResult.columns/rows כפי שהשרת מחזיר
  // (services/reports.py: "המקור היחיד גם ל-JSON וגם ל-CSV וגם ל-PDF") -
  // אין המרה/שכתוב שדות כאן, רק תצוגה.
  // ===================================================================

  var COLUMN_LABELS = {
    pool_id: "מזהה פול", share_class_id: "מזהה סוג מניה", total_shares: 'סה"כ מניות',
    allocated_shares: "הוקצו", unallocated_shares: "פנויות",
    trustee_id: "מזהה נאמן", name: "שם", registration_number: "מספר רישום",
    employee_count: "מספר עובדים", grant_count: "מספר מענקים", total_options: 'סה"כ אופציות',
    vested_options: "הבשילו", unvested_options: "לא הבשילו", exercised_options: "מומשו",
    unexercised_vested_options: "הבשילו ולא מומשו", estimated_value: "שווי משוער",
    employee_id: "מזהה עובד", employee_name: "שם עובד", rule: "כלל", entity_type: "סוג ישות",
    entity_id: "מזהה ישות", title: "כותרת", detail: "פירוט", trigger_date: "תאריך יעד", severity: "חשיבות",
    request_id: "מזהה בקשה", grant_id: "מזהה מענק", options_requested: "כמות מבוקשת", status: "סטטוס",
    requested_on: "הוגשה בתאריך", reviewed_on: "נבדקה בתאריך", reviewed_by_user_id: 'נבדקה ע"י',
    grant_type: "מסלול מס", exercise_price: "מחיר מימוש", currency: "מטבע",
    matched_price_date: "תאריך מחיר תואם", fmv_at_grant_date: "שווי הוגן ביום המענק",
    contribution: "אומדן תרומה", exclusion_reason: "סיבת החרגה", is_estimate: "אומדן", basis: "בסיס החישוב",
    source: "מקור", aggregate_or_entity_type: "סוג ישות/אגרגט", id: "מזהה",
    event_or_action: "אירוע/פעולה", effective_or_occurred_date: "תאריך",
    has_vesting_schedule: "יש לוח הבשלה", has_preceding_stock_price: "יש מחיר FMV קודם למענק",
    has_exercise_price_recorded: "יש מחיר מימוש רשום",
  };
  function columnLabel(col) { return COLUMN_LABELS[col] || col; }

  var NUMERIC_COLUMNS = {
    total_shares: 1, allocated_shares: 1, unallocated_shares: 1, employee_count: 1, grant_count: 1,
    total_options: 1, vested_options: 1, unvested_options: 1, exercised_options: 1,
    unexercised_vested_options: 1, options_requested: 1, exercise_price: 1,
    fmv_at_grant_date: 1, contribution: 1,
  };
  var BOOL_COLUMNS = { has_vesting_schedule: 1, has_preceding_stock_price: 1, has_exercise_price_recorded: 1 };

  function cellHtml(reportType, col, value, row) {
    if (col === "severity") return severityBadge(value);
    if (col === "status" && reportType === "EXERCISE_ACTIVITY") return exerciseStatusBadge(value);
    if (col === "exclusion_reason") return exclusionReasonCell(value);
    // is_estimate תמיד true בדוח הזה (services/reports.py) - כל שורה חייבת
    // להציג את התג, לא רק את הסיכום המצרפי (PLAN v1.1.0 "תיוג אומדן").
    if (col === "is_estimate" && reportType === "COMPENSATION_EXPENSE") return estimateChip();
    // estimated_value בחשיפה-לפי-נאמן שמור ל-v1.4.0 ותמיד null - "לא זמין",
    // *לא* "אומדן" (אין נוסחה בכלל כאן, לא נוסחה-בלי-קלט-מוסמך).
    if (col === "estimated_value" && reportType === "TRUSTEE_EXPOSURE") {
      return notAvailableInline("שדה שמור לגרסה עתידית (v1.4.0, הערכות שווי) - לא מחושב בגרסה זו");
    }
    if (BOOL_COLUMNS[col]) return boolBadge(value);
    if (NUMERIC_COLUMNS[col]) return numberCell(value);
    if (col === "basis" && value) {
      var full = ESOPDocs.escapeHtml(value);
      var shortText = value.length > 42 ? ESOPDocs.escapeHtml(value.slice(0, 42)) + "…" : full;
      return '<span class="text-slate-500" title="' + full + '">' + shortText + "</span>";
    }
    return ESOPDocs.orDash(value);
  }

  function renderTableHead(columns) {
    return columns.map(function (c) { return '<th class="p-3 font-semibold">' + ESOPDocs.escapeHtml(columnLabel(c)) + "</th>"; }).join("");
  }

  // colspan="100": הטבלה דינמית (5-12 עמודות תלוי בסוג הדוח) - ערך גדול-מכוונת
  // הוא הדרך הפשוטה בלי build step לגרום לשורת מצב (טוען/ריק/שגיאה) להשתרע
  // על כל הרוחב בלי לחשב את מספר העמודות המדויק בכל קריאה.
  function loadingRow() {
    return '<tr><td colspan="100" class="p-6 text-center text-slate-500"><i class="fa-solid fa-spinner fa-spin"></i> טוען...</td></tr>';
  }
  function errorRow(message) {
    return '<tr><td colspan="100" class="p-6 text-center text-red-400">' + ESOPDocs.escapeHtml(message) + "</td></tr>";
  }
  function emptyRow(message) {
    return '<tr><td colspan="100" class="p-6 text-center text-slate-500">' + ESOPDocs.escapeHtml(message || "אין שורות להצגה") + "</td></tr>";
  }

  function renderTableBody(reportType, columns, rows) {
    if (!rows.length) return emptyRow();
    return rows.map(function (row) {
      return '<tr class="border-b border-slate-800/50 hover:bg-slate-900/50">' +
        columns.map(function (c) { return '<td class="p-3 text-slate-200 text-xs">' + cellHtml(reportType, c, row[c], row) + "</td>"; }).join("") +
        "</tr>";
    }).join("");
  }

  // ===================================================================
  // סיכום - כל דוח בעל מבנה summary חופשי משלו (services/reports.py) - אין
  // דרך גנרית "נכונה" להציג dict חופשי, ולכן switch מפורש לפי סוג, לא ניחוש.
  // ===================================================================

  function statCard(label, value, accentCls) {
    return '<div class="bg-slate-950 p-4 rounded-2xl border border-slate-800">' +
      '<p class="text-xs text-slate-400 font-medium">' + ESOPDocs.escapeHtml(label) + "</p>" +
      '<h3 class="text-2xl font-black ' + (accentCls || "text-white") + ' mt-1">' + value + "</h3></div>";
  }

  function degradedWarning(ids, context) {
    if (!ids || !ids.length) return "";
    return '<div class="p-3 text-xs bg-amber-500/10 border border-amber-500/30 text-amber-300 rounded-xl">' +
      '<i class="fa-solid fa-triangle-exclamation"></i> לא ניתן היה להעריך ' + ids.length + " " + ESOPDocs.escapeHtml(context) + ": " +
      '<span class="font-mono">' + ESOPDocs.escapeHtml(ids.join(", ")) + "</span></div>";
  }

  function disclosuresBlock(disclosures) {
    if (!disclosures || !disclosures.length) return "";
    return '<div class="p-3 bg-slate-800/60 border border-slate-700 text-slate-400 rounded-2xl text-xs space-y-1">' +
      '<p class="font-bold text-slate-300"><i class="fa-solid fa-circle-info"></i> גילוי נאות</p>' +
      disclosures.map(function (d) { return "<p>" + ESOPDocs.escapeHtml(d) + "</p>"; }).join("") + "</div>";
  }

  function exclusionCountsChips(counts) {
    counts = counts || {};
    return '<div class="flex flex-wrap gap-2">' + EXCLUSION_REASON_ORDER.map(function (reason) {
      var n = counts[reason] || 0;
      return '<span class="text-xs px-2 py-1 rounded-lg bg-rose-500/10 text-rose-400 border border-rose-500/20">' +
        ESOPDocs.escapeHtml(EXCLUSION_REASON_LABELS[reason]) + ": " + n + "</span>";
    }).join("") + "</div>";
  }

  function chipList(entries) {
    // entries: [[label, value, cls]] - שימוש כללי לכל "פירוט לפי X" קטן (by_status,
    // by_tax_track וכו') בלי לבנות טבלה נפרדת לכל אחד.
    return '<div class="flex flex-wrap gap-2">' + entries.map(function (e) {
      var cls = e[2] || "bg-slate-500/10 text-slate-400";
      return '<span class="text-xs px-2 py-1 rounded-lg ' + cls + '">' + ESOPDocs.escapeHtml(e[0]) + ": " + e[1] + "</span>";
    }).join("") + "</div>";
  }

  function renderSummary(reportType, summary, disclosures) {
    summary = summary || {};
    var cards = "";
    var extraParts = [];

    switch (reportType) {
      case "POOL_STATUS":
        cards = statCard("מספר פולים", summary.pool_count) +
          statCard('סה"כ מניות', numberCell(summary.total_shares)) +
          statCard("הוקצו", numberCell(summary.total_allocated), "text-indigo-400") +
          statCard("פנויות", numberCell(summary.total_unallocated), "text-emerald-400");
        break;

      case "TRUSTEE_EXPOSURE":
        cards = statCard("מספר נאמנים", summary.trustee_count);
        // estimated_value שמור ל-v1.4.0 - "לא זמין" ולא "אומדן" (ראו cellHtml).
        extraParts.push(notAvailableBanner(
          "שווי משוער לפי נאמן - לא זמין בגרסה זו",
          "עמודת estimated_value שמורה לגרסה עתידית (v1.4.0, הערכות שווי) ותמיד null כאן - לא מחיר משוער, לא 0."));
        extraParts.push(degradedWarning(summary.degraded_grant_ids, "מענקים (אין להם לוח הבשלה - לא נספרו כ-0)"));
        break;

      case "DEADLINE_RISK":
        cards = statCard('סה"כ פריטים פתוחים', summary.total_open_items) +
          statCard("מוצגים בדוח זה", summary.shown, "text-indigo-400");
        extraParts.push(degradedWarning(summary.degraded_entities, "ישויות"));
        break;

      case "EXERCISE_ACTIVITY": {
        cards = statCard("בקשות בתקופה", summary.request_count);
        var byStatus = summary.by_status || {};
        var chips = Object.keys(byStatus).map(function (s) {
          return [s, byStatus[s], (EXERCISE_STATUS_CLS[s] || "bg-slate-500/10 text-slate-400")];
        });
        if (chips.length) extraParts.push(chipList(chips));
        break;
      }

      case "COMPENSATION_EXPENSE":
        extraParts.push(estimateBanner(
          "אומדן שווי פנימי (Not GAAP Expense) - מבוסס על המחיר הקודם הקרוב ביותר לתאריך המענק בלבד. " +
          "אינו הוצאת ASC 718 רשמית ואינו שווי הוגן (דורש קלטי Black-Scholes/בינומי שאינם ממודלים כאן)."));
        cards = statCard('סה"כ אומדן (כל החברה)', numberCell(summary.total_contribution), "text-amber-400") +
          statCard("מענקים שנכללו", summary.included_grant_count, "text-emerald-400") +
          statCard("מענקים שהוחרגו", summary.excluded_grant_count, "text-rose-400");
        extraParts.push(exclusionCountsChips(summary.exclusion_counts));
        if (summary.by_tax_track && Object.keys(summary.by_tax_track).length) {
          extraParts.push(chipList(Object.keys(summary.by_tax_track).map(function (k) {
            return [k, numberCell(summary.by_tax_track[k]) + " (אומדן)", "bg-amber-500/10 text-amber-400"];
          })));
        }
        break;

      case "MOVEMENT":
        cards = statCard('אירועי ledger בתקופה', summary.ledger_event_count) +
          statCard("אירועי audit log בתקופה", summary.audit_only_event_count, "text-indigo-400");
        // ה-panel הזה מוצג *תמיד* לדוח הזה, לא רק כשיש נתונים חסרים - Trustee
        // אין לו כיסוי ledger/audit בכלל בגרסה זו (PLAN v1.1.0 #6).
        extraParts.push(notAvailableBanner(
          "נאמנים — אין נתוני מעקב זמינים בגרסה זו",
          (summary.trustees && summary.trustees.message) || ""));
        break;

      case "ASC718_READINESS":
        // *** אף מספר כסף לא מוצג כאן בכוונה (PLAN v1.1.0 #7 - הגבלה מפורשת) ***
        cards = statCard('סה"כ מענקים', summary.grant_count) +
          statCard("מוכנים במלואם (שלושת הדגלים)", summary.fully_ready_count, "text-emerald-400");
        break;
    }

    var extra = extraParts.filter(Boolean).join("");
    return '<div class="grid grid-cols-2 md:grid-cols-4 gap-4">' + cards + "</div>" +
      (extra ? '<div class="space-y-2">' + extra + "</div>" : "") +
      disclosuresBlock(disclosures);
  }

  // ===================================================================
  // דשבורד - שני גרפים, בלי ספריית צ'ארטינג (אין אחת בפרויקט, אין build
  // step ל-clients/, ראו PLAN v1.1.0). קוראים ל-build_dashboard בעל
  // המבנה: {as_of, total_grants_in_scope, tax_track_breakdown, forward_vesting_curve,
  // vesting_curve_horizon_months, degraded_grant_ids}.
  // ===================================================================

  var TAX_TRACK_COLORS = ["bg-indigo-500", "bg-emerald-500", "bg-amber-500", "bg-rose-500", "bg-sky-500", "bg-purple-500"];
  var TAX_TRACK_ICONS = {
    IL_102_CAPITAL_GAINS: "fa-piggy-bank", IL_102_WORK_INCOME: "fa-briefcase",
    US_ISO: "fa-flag-usa", US_NSO: "fa-file-signature",
  };
  function taxTrackIcon(key, idx) { return TAX_TRACK_ICONS[key] || ["fa-circle", "fa-square", "fa-diamond"][idx % 3]; }

  // bar אופקי מוערם - כל מקטע ברוחב יחסי ל-pct_of_total. legend מתחת עם
  // אייקון+טקסט לכל מסלול (לא צבע בלבד - PLAN v1.1.0, לוח אנליטי).
  function renderTaxTrackBar(breakdown) {
    if (!breakdown || !breakdown.length) {
      return '<p class="text-sm text-slate-500 text-center py-6">אין מענקים בהיקף החברה</p>';
    }
    var segments = breakdown.map(function (b, i) {
      var color = TAX_TRACK_COLORS[i % TAX_TRACK_COLORS.length];
      var pct = b.pct_of_total || 0;
      return '<div class="' + color + ' h-full" style="width:' + pct + '%" ' +
        'title="' + ESOPDocs.escapeHtml(b.grant_type) + ": " + b.count + " (" + pct + "%)" + '"></div>';
    }).join("");
    var legend = breakdown.map(function (b, i) {
      var color = TAX_TRACK_COLORS[i % TAX_TRACK_COLORS.length];
      return '<span class="flex items-center gap-1.5 text-xs text-slate-300">' +
        '<span class="w-3 h-3 rounded ' + color + ' inline-flex items-center justify-center">' +
          '<i class="fa-solid ' + taxTrackIcon(b.grant_type, i) + ' text-[7px] text-slate-950"></i></span>' +
        ESOPDocs.escapeHtml(b.grant_type) + ' <span class="text-slate-500">(' + b.count + " · " + b.pct_of_total + "%)</span></span>";
    }).join("");
    return '<div class="flex h-8 w-full rounded-lg overflow-hidden border border-slate-800">' + segments + "</div>" +
      '<div class="flex flex-wrap gap-4 mt-4">' + legend + "</div>";
  }

  // עקומת הבשלה קדימה - SVG polyline, מחושב מלוח הבשלה שהשרת החזיר (לא מחשוב
  // תאריכים/כספים מחדש בצד לקוח - services/engine.py כבר חישב כל נקודה).
  //
  // *** dir="ltr" מפורש על עוטף הגיאומטריה בלבד (לא על כל הכרטיס) *** - ציר
  // הזמן משמאל-לימין הוא המוסכמה הפיננסית המקובלת; אם היינו עוטפים ב-dir
  // ברירת המחדל של הדף (rtl), הדפדפן היה מראה את הפוליליין/ה-legend הפוכים
  // בלי שום שינוי בקוד שלנו - היפוך שקט. תוויות בעברית ממוקמות כ-HTML נפרד
  // *מחוץ* לקואורדינטות ה-SVG (לא <text> בתוכו) בדיוק כדי שהן לא ייצאו
  // מהפיכה אם מישהו בעתיד יעטוף את ה-SVG עצמו ב-rtl.
  function renderVestingCurve(points, horizonMonths, asOfStr) {
    if (!points || points.length < 2) {
      return '<p class="text-sm text-slate-500 text-center py-6">אין מספיק נתונים לעקומת הבשלה</p>';
    }
    var W = 680, H = 200, PAD_X = 36, TOP = 18, BOTTOM = 40;
    var n = points.length;
    var values = points.map(function (p) { return p.cumulative_vested || 0; });
    var maxV = Math.max.apply(null, values.concat([1]));

    function x(i) { return PAD_X + (i * (W - 2 * PAD_X)) / (n - 1); }
    function xPct(i) { return (x(i) / W) * 100; }
    function y(v) { return H - BOTTOM - ((v / maxV) * (H - BOTTOM - TOP)); }

    // todayIdx: אינדקס אחרון שכבר "קרה" (as_of <= היום שהשרת שלח) - בפועל
    // ברוב המקרים רק הנקודה הראשונה (עוגן לתחילת החודש הנוכחי, ראו
    // services/reports.py::_monthly_grid), כל השאר תחזית קדימה.
    var todayIdx = 0;
    for (var i = 0; i < n; i++) {
      if (points[i].as_of <= asOfStr) todayIdx = i; else break;
    }

    var solidPoints = [];
    for (var s = 0; s <= todayIdx; s++) solidPoints.push(x(s) + "," + y(values[s]));
    var dashedPoints = [];
    for (var d = todayIdx; d < n; d++) dashedPoints.push(x(d) + "," + y(values[d]));
    // קו מלא נדרש לשתי נקודות לפחות. הרשת שהשרת מחזיר עוגנת ב-1 בחודש הנוכחי
    // (_monthly_grid), כך שברוב המקרים יש נקודת-עבר *אחת* בלבד - ואז אין קו
    // מלא לצייר, רק הנקודה המסומנת. הלגנד למטה מותנה בזה ולא מבטיח קו שלא קיים.
    var hasSolidSegment = solidPoints.length > 1;

    var xToday = x(todayIdx), yToday = y(values[todayIdx]);

    // תוויות ציר X: כל 6 חודשים בלבד (37 נקודות היו נדחסות זו על זו) + תמיד
    // הנקודה האחרונה, ממוקמות ב-% מרוחב המכל (preserveAspectRatio="none" למטה
    // מבטיח שה-SVG נמתח לרוחב המכל בדיוק, כך שאחוזים אלה תמיד תואמים ל-x(i)).
    var labelIdxs = [];
    for (var li = 0; li < n; li += 6) labelIdxs.push(li);
    if (labelIdxs[labelIdxs.length - 1] !== n - 1) labelIdxs.push(n - 1);
    var xLabels = labelIdxs.map(function (idx) {
      var d = new Date(points[idx].as_of + "T00:00:00Z");
      var text = isNaN(d.getTime()) ? points[idx].as_of : d.toLocaleDateString("he-IL", { month: "short", year: "2-digit", timeZone: "UTC" });
      // top ממוקם בתוך רצועת ה-padding-bottom של העוטף ולא על 100%: העוטף הוא
      // border-box, ולכן top:100% נופל על הקצה התחתון ממש והתוויות גלשו אל
      // הלג'נד שמתחת (נמדד: חריגה של 15px, חפיפה של 7px).
      return '<span class="absolute text-[10px] text-slate-500 -translate-x-1/2" style="left:' + xPct(idx).toFixed(2) + '%; top:calc(100% - 24px)">' + ESOPDocs.escapeHtml(text) + "</span>";
    }).join("");

    var svg =
      '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" class="w-full block" style="height:' + H + 'px" role="img" aria-label="עקומת הבשלה קדימה">' +
        '<line x1="' + PAD_X + '" y1="' + (H - BOTTOM) + '" x2="' + (W - PAD_X) + '" y2="' + (H - BOTTOM) + '" stroke="#334155" stroke-width="1"/>' +
        '<line x1="' + xToday + '" y1="' + TOP + '" x2="' + xToday + '" y2="' + (H - BOTTOM) + '" stroke="#f59e0b" stroke-width="1" stroke-dasharray="3,3"/>' +
        (hasSolidSegment ? '<polyline points="' + solidPoints.join(" ") + '" fill="none" stroke="#818cf8" stroke-width="2.5"/>' : "") +
        (dashedPoints.length > 1 ? '<polyline points="' + dashedPoints.join(" ") + '" fill="none" stroke="#818cf8" stroke-width="2.5" stroke-dasharray="7,5"/>' : "") +
        '<circle cx="' + xToday + '" cy="' + yToday + '" r="4" fill="#f59e0b"/>' +
      "</svg>";

    return (
      '<div dir="ltr" class="relative" style="height:' + (H + 28) + "px; padding-bottom:28px\">" +
        svg +
        '<span class="absolute text-[10px] text-slate-500" style="left:0; top:0">' + numberCell(maxV) + " אופציות</span>" +
        '<span class="absolute text-[10px] text-slate-500" style="left:0; top:' + (H - BOTTOM - 14) + 'px">0</span>' +
        xLabels +
      "</div>" +
      // legend - עברית, בזרימת המסמך הרגילה (rtl) ולא בתוך העוטף ה-ltr, ואייקון+
      // טקסט לכל פריט (לא צבע בלבד) בדיוק כמו שאר האפליקציה.
      '<div class="flex flex-wrap items-center gap-4 mt-2 text-xs text-slate-400">' +
        (hasSolidSegment
          ? '<span class="flex items-center gap-1.5"><span class="inline-block w-4 h-0.5 bg-indigo-400"></span> קו מלא - הבשלה שכבר קרתה</span>'
          : "") +
        '<span class="flex items-center gap-1.5"><span class="inline-block w-4 h-0.5 border-t-2 border-dashed border-indigo-400"></span> קו מקווקו - תחזית לפי לוחות ההבשלה הקיימים בלבד</span>' +
        '<span class="flex items-center gap-1.5"><i class="fa-solid fa-circle text-amber-400"></i> ' +
          (hasSolidSegment ? "היום" : "הבשלה שכבר קרתה, נכון להיום") + " (" + ESOPDocs.escapeHtml(asOfStr) + ")</span>" +
        '<span class="text-slate-600">אופק: ' + horizonMonths + " חודשים קדימה</span>" +
      "</div>"
    );
  }

  global.ESOPReports = {
    REPORT_TYPES: REPORT_TYPES,
    reportTypeMeta: reportTypeMeta,
    endpointPath: endpointPath,
    reportTypeOptionsHtml: reportTypeOptionsHtml,
    savedReportOptionsHtml: savedReportOptionsHtml,
    renderTableHead: renderTableHead,
    renderTableBody: renderTableBody,
    loadingRow: loadingRow,
    errorRow: errorRow,
    emptyRow: emptyRow,
    renderSummary: renderSummary,
    renderTaxTrackBar: renderTaxTrackBar,
    renderVestingCurve: renderVestingCurve,
    degradedWarning: degradedWarning,
    numberCell: numberCell,
    estimateChip: estimateChip,
    notAvailableInline: notAvailableInline,
  };
})(window);
