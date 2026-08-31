"""programing files/import_syllabus.py の clean_name()・classify_kyoyo() は、科目名の
【遠隔】【再履修】タグを削除せず末尾に（遠隔）（再履修）として残す（course_sections/syllabi の
一意制約で通常クラス側に潰れて消えるのを防ぐため、2026-08-31導入）。

両方のタグが別々の【】で同時に付く科目名（【遠隔】【再履修】等）で、片方だけ拾って
落としてしまうと再び一意制約衝突に戻ってしまうバグが無いことを確認する。
"""
import sys
from pathlib import Path

# programing files/models.py・database.py はルート直下の同名モジュールと衝突するため、
# sys.pathへの追加はimport_syllabus本体（asyncio/re/pathlib/_envしか使わない）の
# import中だけに限定し、完了後は必ず取り除く。追加したままにすると、後続で収集される
# 他のテストファイルがimport modelsした際にこちらのprograming files/models.py
# （別スキーマ・DATABASE_URL前提）を誤って掴んでしまう（実際に発生を確認済み）。
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "programing files")
_inserted = _SCRIPTS_DIR not in sys.path
if _inserted:
    sys.path.insert(0, _SCRIPTS_DIR)
try:
    import import_syllabus as isy  # noqa: E402
finally:
    if _inserted:
        sys.path.remove(_SCRIPTS_DIR)


def test_clean_name_keeps_both_remote_and_retake_tags():
    raw = "力学基礎1【遠隔】【再履修】"
    assert isy.clean_name(raw) == "力学基礎1（遠隔）（再履修）"


def test_clean_name_keeps_single_tag():
    assert isy.clean_name("力学基礎1【遠隔】") == "力学基礎1（遠隔）"
    assert isy.clean_name("力学基礎1【再履修】") == "力学基礎1（再履修）"


def test_clean_name_drops_unrelated_bracket_tags():
    assert isy.clean_name("不動産学入門【不動産】") == "不動産学入門"


def test_classify_kyoyo_strips_both_tags_before_lookup():
    base_name = next(iter(isy._KYOYO_NAME_TO_CLASSIFICATION))
    expected = isy._KYOYO_NAME_TO_CLASSIFICATION[base_name]

    assert isy.classify_kyoyo(base_name) == expected
    assert isy.classify_kyoyo(base_name + "（遠隔）") == expected
    assert isy.classify_kyoyo(base_name + "（再履修）") == expected
    assert isy.classify_kyoyo(base_name + "（遠隔）（再履修）") == expected
