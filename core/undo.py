"""科目管理画面の「元に戻す」用に、直前の削除内容を1件だけプロセスメモリ上に保持する。
core/cache.pyの各キャッシュと同じくワーカープロセスごとに独立する（WEB_CONCURRENCY>1では
削除したワーカーと元に戻すワーカーが別になり得るが、管理画面の誤操作救済という用途上、
DBに永続化するほどの重要性はないため許容する）。
"""
import time

_TTL_SECONDS = 600  # この時間を過ぎた削除は「元に戻す」対象から外す

_last_deleted: dict | None = None


def set_last_deleted(subjects: list[dict]) -> None:
    global _last_deleted
    _last_deleted = {"created_at": time.monotonic(), "subjects": subjects}


def pop_last_deleted() -> list[dict] | None:
    """直前の削除内容を取り出して消費する（1回限り、連打で二重復元されないように）。"""
    global _last_deleted
    snap = _last_deleted
    _last_deleted = None
    if snap is None:
        return None
    if time.monotonic() - snap["created_at"] > _TTL_SECONDS:
        return None
    return snap["subjects"]


def has_last_deleted() -> bool:
    """「戻る」ボタンを常時表示する科目管理画面用に、消費せず存在有無だけ確認する。"""
    return _last_deleted is not None and time.monotonic() - _last_deleted["created_at"] <= _TTL_SECONDS
