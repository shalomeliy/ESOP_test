/* מרכז ההתראות - מימוש אחד לשלושת הפורטלים.
 *
 * למה קובץ משותף ולא קוד בכל פורטל: הלוגיקה זהה לחלוטין בשלושתם (אותם endpoints,
 * אותם 5 כללים, אותה סמנטיקה של סגירה), וההבדל היחיד הוא צבע המבטא. שלושה עותקים
 * היו נסחפים זה מזה בגרסה הראשונה שתשנה כלל.
 *
 * הפורטלים פונים ל-API בכתובת מוחלטת, ולכן הקובץ הזה עובד גם כשה-HTML נפתח
 * כקובץ מקומי וגם כשהוא מוגש מ-/clients ע"י FastAPI.
 */
(function (global) {
  "use strict";

  // תוויות הכללים. lead_days *לא* אומר אותו דבר בכל כלל: בשלושת הראשונים זה
  // "כמה ימים לפני האירוע להתריע", ובשניים האחרונים "אחרי כמה ימים של חוסר
  // מעש להתריע". שתי המשמעויות חיות באותה עמודה ב-DB, ולכן ההסבר חייב להופיע
  // לכל שדה בנפרד - אחרת המשתמש מכייל את הכיוון ההפוך.
  var RULES = {
    VESTING_EVENT_NEAR: {
      title: "אירוע הבשלה מתקרב",
      leadLabel: "להתריע כמה ימים *לפני* שאופציות נוספות מבשילות",
    },
    TRUSTEE_HOLDING_ENDING: {
      title: "חסימת נאמנות מסתיימת",
      leadLabel: "להתריע כמה ימים *לפני* תום 24 חודשי החסימה (סעיף 102)",
    },
    PTEW_CLOSING: {
      title: "חלון מימוש לאחר עזיבה נסגר",
      leadLabel: "להתריע כמה ימים *לפני* סגירת החלון",
    },
    REQUEST_PENDING_TOO_LONG: {
      title: "בקשת מימוש ממתינה",
      leadLabel: "להתריע *אחרי* כמה ימי המתנה לאישור",
    },
    FULLY_VESTED_UNEXERCISED: {
      title: "מענק הבשיל ולא מומש",
      leadLabel: "להתריע *אחרי* כמה ימים מההבשלה המלאה",
    },
  };

  var SEVERITY = {
    critical: { dot: "bg-rose-500", text: "text-rose-400", border: "border-rose-500/30" },
    warning: { dot: "bg-amber-500", text: "text-amber-400", border: "border-amber-500/30" },
    info: { dot: "bg-sky-500", text: "text-sky-400", border: "border-sky-500/30" },
  };

  var cfg = null;
  var timer = null;
  var state = { items: [], total: 0, degraded: [], open: false, prefsOpen: false, prefs: [] };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function mount() {
    return document.getElementById("notification-mount");
  }

  async function api(path, options) {
    var res = await fetch(cfg.api + path, Object.assign({ headers: cfg.authHeaders() }, options || {}));
    if (!res.ok) throw new Error((await res.text()) || "שגיאה " + res.status);
    return res.status === 204 ? null : res.json();
  }

  // ---------------------------------------------------------------------------
  // נתונים
  // ---------------------------------------------------------------------------

  async function refresh() {
    try {
      // שתי קריאות ולא אחת: הפיד חסום ב-50 פריטים, והמונה מדווח את הסך האמיתי.
      // לספור את items.length היה מציג "50" כאילו זו העובדה ולא התקרה.
      var feed = await api("/notifications");
      var count = await api("/notifications/unread-count");
      state.items = feed.items || [];
      state.total = count.count;
      state.degraded = feed.degraded_entities || [];
      render();
    } catch (err) {
      // כשל בהתראות לא מפיל את המסך הראשי - הן מסך משני.
      var badge = document.getElementById("notif-badge");
      if (badge) badge.classList.add("hidden");
    }
  }

  async function dismiss(key) {
    try {
      await api("/notifications/" + encodeURIComponent(key) + "/dismiss", { method: "POST" });
      await refresh();
    } catch (err) {
      if (cfg.toast) cfg.toast("סגירת ההתראה נכשלה: " + err.message, true);
    }
  }

  async function loadPrefs() {
    var data = await api("/notifications/preferences");
    state.prefs = data.preferences || [];
  }

  async function savePrefs() {
    var payload = { preferences: state.prefs.map(function (p) {
      var input = document.getElementById("pref-lead-" + p.rule);
      var toggle = document.getElementById("pref-on-" + p.rule);
      return { rule: p.rule, enabled: toggle.checked, lead_days: parseInt(input.value, 10) };
    }) };
    try {
      var data = await api("/notifications/preferences", {
        method: "PUT",
        headers: Object.assign(cfg.authHeaders(), { "Content-Type": "application/json" }),
        body: JSON.stringify(payload),
      });
      state.prefs = data.preferences || [];
      if (cfg.toast) cfg.toast("העדפות ההתראות נשמרו");
      state.prefsOpen = false;
      await refresh();
    } catch (err) {
      // השרת דוחה כלל לא מוכר ו-lead_days שלילי. מציגים את הסיבה שלו כמו שהיא.
      if (cfg.toast) cfg.toast("שמירה נכשלה: " + err.message, true);
    }
  }

  // ---------------------------------------------------------------------------
  // תצוגה
  // ---------------------------------------------------------------------------

  function itemHtml(item) {
    var sev = SEVERITY[item.severity] || SEVERITY.info;
    return (
      '<li class="flex gap-3 p-3 border-b border-slate-800 last:border-0 hover:bg-slate-900/60">' +
        '<span class="w-2 h-2 rounded-full mt-2 shrink-0 ' + sev.dot + '"></span>' +
        '<div class="flex-1 min-w-0">' +
          '<div class="text-sm font-semibold text-white">' + esc(item.title) + "</div>" +
          '<div class="text-xs text-slate-400 mt-0.5 break-words">' + esc(item.detail) + "</div>" +
          '<div class="text-[11px] text-slate-600 mt-1 font-mono">' + esc(item.entity_id) + "</div>" +
        "</div>" +
        '<button title="סגור התראה" data-notif-key="' + esc(item.key) + '" ' +
                'class="notif-dismiss text-slate-500 hover:text-white shrink-0 px-1">&times;</button>' +
      "</li>"
    );
  }

  function panelHtml() {
    var body;
    if (state.items.length === 0) {
      body = '<li class="p-6 text-center text-sm text-slate-500">אין התראות פעילות</li>';
    } else {
      body = state.items.map(itemHtml).join("");
    }

    // degraded_entities = ישויות שהמנוע לא הצליח להעריך. מוצגות במפורש ולא
    // מושתקות: התראה שלא נוצרה בגלל נתון חסר היא בדיוק המצב שבו המשתמש חושב
    // שהכל תקין.
    var degraded = state.degraded.length
      ? '<div class="p-3 text-xs bg-amber-500/10 border-b border-amber-500/30 text-amber-300">' +
        "לא ניתן היה להעריך " + state.degraded.length + " ישויות: " +
        '<span class="font-mono">' + esc(state.degraded.join(", ")) + "</span>" +
        "</div>"
      : "";

    var capped = state.total > state.items.length
      ? '<div class="p-2 text-center text-[11px] text-slate-500 border-t border-slate-800">' +
        "מוצגות " + state.items.length + " מתוך " + state.total + "</div>"
      : "";

    return (
      '<div id="notif-panel" class="absolute left-0 mt-2 w-96 max-w-[90vw] bg-slate-950 border border-slate-700 ' +
           'rounded-xl shadow-2xl z-50 overflow-hidden">' +
        '<div class="flex items-center justify-between p-3 border-b border-slate-800">' +
          '<span class="font-bold text-white text-sm">התראות</span>' +
          '<button id="notif-prefs-open" class="text-xs text-slate-400 hover:text-white">' +
            '<i class="fa-solid fa-sliders"></i> העדפות</button>' +
        "</div>" +
        degraded +
        '<ul class="max-h-80 overflow-y-auto">' + body + "</ul>" +
        capped +
      "</div>"
    );
  }

  function prefsHtml() {
    var rows = state.prefs.map(function (p) {
      var meta = RULES[p.rule] || { title: p.rule, leadLabel: "ימים" };
      return (
        '<div class="p-3 border border-slate-800 rounded-xl bg-slate-950/60">' +
          '<label class="flex items-center gap-3 cursor-pointer">' +
            '<input type="checkbox" id="pref-on-' + p.rule + '" ' + (p.enabled ? "checked" : "") +
                   ' class="w-4 h-4 accent-current">' +
            '<span class="font-semibold text-white text-sm">' + esc(meta.title) + "</span>" +
            '<span class="text-[11px] text-slate-600 font-mono mr-auto">' + p.rule + "</span>" +
          "</label>" +
          '<div class="mt-2 flex items-center gap-2">' +
            '<input type="number" min="0" id="pref-lead-' + p.rule + '" value="' + p.lead_days + '" ' +
                   'class="w-20 bg-slate-900 border border-slate-700 rounded-lg px-2 py-1 text-sm text-white">' +
            '<span class="text-xs text-slate-400">' + esc(meta.leadLabel) + "</span>" +
          "</div>" +
        "</div>"
      );
    }).join("");

    return (
      '<div id="notif-prefs" class="fixed inset-0 bg-black/60 flex items-center justify-center z-[60] p-4">' +
        '<div class="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-lg max-h-[85vh] overflow-y-auto">' +
          '<div class="flex items-center justify-between p-4 border-b border-slate-800">' +
            '<h3 class="font-bold text-white">העדפות התראות</h3>' +
            '<button id="notif-prefs-close" class="text-slate-400 hover:text-white text-xl">&times;</button>' +
          "</div>" +
          '<div class="p-4 space-y-3">' +
            '<p class="text-xs text-slate-500">התראות הן opt-out: כלל שלא הוגדר במפורש דלוק.</p>' +
            rows +
          "</div>" +
          '<div class="p-4 border-t border-slate-800 flex justify-end gap-2">' +
            '<button id="notif-prefs-cancel" class="px-4 py-2 text-sm text-slate-400 hover:text-white">ביטול</button>' +
            '<button id="notif-prefs-save" class="px-4 py-2 text-sm text-white rounded-xl ' + cfg.accentBtn + '">שמור</button>' +
          "</div>" +
        "</div>" +
      "</div>"
    );
  }

  function render() {
    var host = mount();
    if (!host) return;

    var badgeHidden = state.total === 0 ? " hidden" : "";
    host.innerHTML =
      '<button id="notif-bell" title="התראות" aria-label="התראות" ' +
              'class="relative w-10 h-10 rounded-xl bg-slate-950 border border-slate-700 text-slate-300 ' +
              'hover:text-white hover:border-slate-500 transition">' +
        '<i class="fa-solid fa-bell"></i>' +
        '<span id="notif-badge" class="absolute -top-1 -left-1 min-w-[18px] h-[18px] px-1 rounded-full ' +
              'bg-rose-600 text-white text-[11px] font-bold flex items-center justify-center' + badgeHidden + '">' +
          (state.total > 99 ? "99+" : state.total) +
        "</span>" +
      "</button>" +
      (state.open ? panelHtml() : "") +
      (state.prefsOpen ? prefsHtml() : "");

    wire();
  }

  function wire() {
    var bell = document.getElementById("notif-bell");
    if (bell) bell.onclick = function (e) {
      e.stopPropagation();
      state.open = !state.open;
      render();
    };

    Array.prototype.forEach.call(document.querySelectorAll(".notif-dismiss"), function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        dismiss(btn.getAttribute("data-notif-key"));
      };
    });

    var openPrefs = document.getElementById("notif-prefs-open");
    if (openPrefs) openPrefs.onclick = async function (e) {
      e.stopPropagation();
      try {
        await loadPrefs();
        state.prefsOpen = true;
        state.open = false;
        render();
      } catch (err) {
        if (cfg.toast) cfg.toast("טעינת ההעדפות נכשלה: " + err.message, true);
      }
    };

    ["notif-prefs-close", "notif-prefs-cancel"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.onclick = function () { state.prefsOpen = false; render(); };
    });

    var save = document.getElementById("notif-prefs-save");
    if (save) save.onclick = function () { savePrefs().then(render); };
  }

  // סגירת הפאנל בלחיצה בחוץ. לא נוגע במודאל ההעדפות, שנסגר רק בכפתור מפורש
  // כדי שלחיצה מחוץ לו לא תזרוק שינויים שהמשתמש הקליד.
  document.addEventListener("click", function () {
    if (state.open) { state.open = false; render(); }
  });

  // ---------------------------------------------------------------------------
  // API ציבורי
  // ---------------------------------------------------------------------------

  var ESOPNotifications = {
    /* cfg: { api, authHeaders, toast, accentBtn, pollMs } */
    init: function (options) {
      cfg = Object.assign({ pollMs: 60000, accentBtn: "bg-slate-700 hover:bg-slate-600" }, options);
      state = { items: [], total: 0, degraded: [], open: false, prefsOpen: false, prefs: [] };
      render();
      refresh();
      // רענון תקופתי: הפיד מחושב על קריאה, ולכן דדליין שהתקרב בזמן שהמסך פתוח
      // לא יופיע בלי משיכה מחדש.
      if (timer) clearInterval(timer);
      timer = setInterval(refresh, cfg.pollMs);
    },

    stop: function () {
      if (timer) clearInterval(timer);
      timer = null;
      state = { items: [], total: 0, degraded: [], open: false, prefsOpen: false, prefs: [] };
      var host = mount();
      if (host) host.innerHTML = "";
    },

    refresh: refresh,
  };

  global.ESOPNotifications = ESOPNotifications;
})(window);
