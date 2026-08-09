#!/usr/bin/env python3
"""מדד צבירת הקשר — מתריע מתי כדאי לפתוח שיחה חדשה או להריץ /clear.

למה זה קיים
-----------
ב-06/08/2026 התגלה שסשן אחד הגיע ל-79M טוקנים ו-1,031 תורים, כי שלוש גרסאות
נבנו בו בלי לסגור. כל תור קורא מחדש את כל מה שנכתב לפניו, כך שהעלות גדלה עם
אורך השיחה ולא עם קושי המשימה. הסקריפט הזה הופך את זה לנראה לעין.

שתי דרכי הפעלה
--------------
1. statusline — קלוד קוד מריץ אותו אוטומטית ומזרים JSON ל-stdin. מדפיס שורה
   אחת קצרה. אפס עלות טוקנים: זו תצוגה, לא קונטקסט.
2. ידנית — `python tools/context_check.py` בלי stdin. מוצא לבד את הסשן
   האחרון של הפרויקט ומדפיס דוח מלא.

הערכת הטוקנים היא **הערכה גסה** מגודל קובץ ה-transcript. הוא כולל פלט כלים
ומטא-דאטה, ולכן נוטה להגזים מול מה ש-/context מדווח. זה מכוון: זהו מדד מגמה,
לא חשבונאות. למספר המדויק — /context.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Windows console defaults to cp1252, which can't encode the 🔴/🟡/🟢 icons below.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------- ספי התראה
# נגזרו מהמקרה האמיתי: 1,031 תורים = 79M טוקנים. הספים כאן שמרניים בכוונה —
# הנזק מניקוי מוקדם מדי הוא אפס (הקבצים זוכרים), הנזק ממאוחר מדי הוא מצטבר.
TURNS_WATCH, TURNS_CLEAN = 80, 200
MB_WATCH, MB_CLEAN = 2.0, 6.0


def _transcript_from_stdin() -> Path | None:
    """במצב statusline קלוד קוד מזרים JSON עם transcript_path."""
    if sys.stdin is None or sys.stdin.isatty():
        return None
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return None
        p = json.loads(raw).get("transcript_path")
        return Path(p) if p else None
    except Exception:
        return None


def _newest_transcript() -> Path | None:
    """מצב ידני: מחפש את ה-transcript האחרון של הפרויקט הנוכחי.

    קלוד קוד שומר תעתיקים ב-~/.claude/projects/<נתיב-מקודד>/<session>.jsonl
    כשהנתיב מקודד עם מקפים במקום מפרידי תיקיות ונקודתיים.
    """
    root = Path.home() / ".claude" / "projects"
    if not root.is_dir():
        return None
    cwd = str(Path.cwd().resolve())
    slug = cwd.replace(":", "-").replace("\\", "-").replace("/", "-")
    candidates = list((root / slug).glob("*.jsonl")) if (root / slug).is_dir() else []
    if not candidates:  # נפילה אחורה: כל הפרויקטים, לפי זמן שינוי
        candidates = list(root.glob("*/*.jsonl"))
    return max(candidates, key=lambda p: p.stat().st_mtime, default=None)


def measure(path: Path) -> dict:
    size = path.stat().st_size
    turns = 0
    with path.open("rb") as fh:
        for line in fh:
            if line.strip():
                turns += 1
    return {"turns": turns, "mb": size / 1_048_576, "est_tokens": size // 4}


def verdict(m: dict) -> tuple[str, str, str]:
    """מחזיר (סמל, מילה, מה לעשות)."""
    if m["turns"] >= TURNS_CLEAN or m["mb"] >= MB_CLEAN:
        return "🔴", "clean now", "פתח שיחה חדשה. עדכן HANDOFF.md לפני."
    if m["turns"] >= TURNS_WATCH or m["mb"] >= MB_WATCH:
        return "🟡", "watch", "בגבול. אם השלב הנוכחי נסגר ואומת — זה הרגע."
    return "🟢", "ok", "אין צורך לנקות."


def main() -> int:
    path = _transcript_from_stdin()
    statusline = path is not None
    if path is None:
        path = _newest_transcript()

    if path is None or not path.exists():
        print("ctx: n/a" if statusline else "לא נמצא transcript. הרץ מתוך שורש הפרויקט.")
        return 0

    m = measure(path)
    icon, word, advice = verdict(m)

    if statusline:
        # שורה אחת, קצרה — זו שורת מצב.
        print(f"{icon} ctx {m['turns']}t · {m['mb']:.1f}MB · ~{m['est_tokens']//1000}k · {word}")
        return 0

    print(f"{icon}  {word.upper()}")
    print()
    print(f"  תורים בשיחה     {m['turns']:>8,}   (צפוי לעין: {TURNS_WATCH} · לנקות: {TURNS_CLEAN})")
    print(f"  גודל transcript {m['mb']:>8.1f}MB (צפוי לעין: {MB_WATCH} · לנקות: {MB_CLEAN})")
    print(f"  טוקנים מוערכים  ~{m['est_tokens']//1000:>7,}k  (הערכה גסה — למספר מדויק: /context)")
    print()
    print(f"  {advice}")
    print()
    print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
