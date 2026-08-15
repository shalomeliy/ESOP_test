"""tools/sweep_orphan_files.py - הסיווג שמפריד קובץ יתום מקובץ מוגן.

הבדיקה קיימת בגלל ``--delete``: הכלי מוחק בלתי-הפיך מתיקיות של המשתתף, וטעות
בכיוון אחד (סיווג קובץ מופנה כיתום) שוברת ייבוא עובד או מחזירה 500 על מסמך.
לכן נבדק דווקא הכיוון הזה, ולא רק "יתום מזוהה כיתום".

הכלי עצמו נכתב ב-v1.1.1 פריט ד1 אחרי שנמצאו 3,449 קבצים ב-export_store שאף שורה
ב-data_transfer_runs לא מפנה אליהם.
"""

from pathlib import Path

import pytest

from tools.sweep_orphan_files import _scan


@pytest.fixture
def store(tmp_path, monkeypatch):
    """מפנה את PROJECT_ROOT של הכלי ל-tmp_path. הכלי נגזר מ-``__file__``, בדיוק
    כמו שני ה-store-ים שהוא סורק - ולכן בלי העקיפה הבדיקה הייתה סורקת את
    התיקיות האמיתיות של הפרויקט (ראו conftest.py::isolated_file_stores)."""
    import tools.sweep_orphan_files as sweep_module

    monkeypatch.setattr(sweep_module, "PROJECT_ROOT", tmp_path)
    (tmp_path / "export_store").mkdir()
    return tmp_path / "export_store"


def test_a_referenced_file_is_never_classified_as_an_orphan(store):
    """הכיוון המסוכן. bundle שיש לו שורה ב-data_transfer_runs נקרא שוב כדי לבצע
    IMPORT_COMMIT - מחיקתו הופכת ייבוא תקין ל-500."""
    (store / "bundle-keep.json").write_text("{}", encoding="utf-8")
    (store / "bundle-drop.json").write_text("{}", encoding="utf-8")

    orphans, _ = _scan("export_store", referenced={"bundle-keep.json"})

    assert [p.name for p in orphans] == ["bundle-drop.json"]


def test_a_file_in_a_subdirectory_is_still_matched_by_name(store):
    """ההשוואה על basename ולא על הנתיב המלא היא הכרעה מכוונת: פריסת תתי-תיקיות
    עתידית (למשל לפי חברה) לא תהפוך קובץ מוגן ליתום בשקט. הגנת-יתר היא הכיוון
    הנכון לטעות בו כשהפעולה היא מחיקה."""
    nested = store / "COMP-1"
    nested.mkdir()
    (nested / "bundle-keep.json").write_text("{}", encoding="utf-8")

    orphans, _ = _scan("export_store", referenced={"bundle-keep.json"})

    assert orphans == []


def test_reported_size_counts_only_the_orphans(store):
    (store / "keep.json").write_text("x" * 100, encoding="utf-8")
    (store / "drop.json").write_text("y" * 40, encoding="utf-8")

    orphans, total = _scan("export_store", referenced={"keep.json"})

    assert len(orphans) == 1
    assert total == 40, "הגודל המדווח כולל קבצים שלא יימחקו"


def test_a_missing_store_directory_is_not_an_error(tmp_path, monkeypatch):
    """התיקיות git-ignored, כלומר בקלון חדש הן פשוט לא קיימות."""
    import tools.sweep_orphan_files as sweep_module

    monkeypatch.setattr(sweep_module, "PROJECT_ROOT", tmp_path)

    assert _scan("export_store", referenced=set()) == ([], 0)
