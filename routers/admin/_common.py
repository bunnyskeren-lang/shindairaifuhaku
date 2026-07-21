"""admin系ルーター間で共有する並び替え(up/down)処理のヘルパー。

instructor/faculty/course/classification/credit_requirement_group の各
「上へ/下へ」エンドポイントが、対象を1つ隣にスワップして並び順を振り直す
という同一パターンをそれぞれ個別実装していたため、ここに集約する。
"""
from sqlalchemy import select

from models import DisplayOrder


def reorder_sort_order(items: list, item_id, direction: str) -> bool:
    """sort_order列を持つORMオブジェクトのリストitemsの中からitem_idを探し、
    directionへ1つ移動してitems全体のsort_orderを0からの連番に振り直す
    （呼び出し側でsession.commit()すること）。
    item_idが見つからなければFalseを返す。既に端で移動できない場合もTrueを返す。
    """
    try:
        idx = next(i for i, obj in enumerate(items) if obj.id == item_id)
    except StopIteration:
        return False
    delta = -1 if direction == "up" else 1
    swap_idx = idx + delta
    if 0 <= swap_idx < len(items):
        items[idx], items[swap_idx] = items[swap_idx], items[idx]
        for i, obj in enumerate(items):
            obj.sort_order = i
    return True


def swap_by_index(items: list, key, direction: str) -> bool | None:
    """items(値のリスト)の中からkeyと等しい要素を探し、directionへ1つ移動する
    （リストは破壊的に変更される）。戻り値: keyが見つからなければNone、
    見つかったが既に端で移動できなければFalse、実際にスワップしたらTrue。
    """
    try:
        idx = items.index(key)
    except ValueError:
        return None
    delta = -1 if direction == "up" else 1
    swap_idx = idx + delta
    if 0 <= swap_idx < len(items):
        items[idx], items[swap_idx] = items[swap_idx], items[idx]
        return True
    return False


async def upsert_display_order_sequence(session, kind: str, names: list, faculty: str | None = None) -> None:
    """DisplayOrder(kind, name[, faculty])行をnamesの並び順でsort_order=0..nに
    一括更新/新規作成する（呼び出し側でsession.commit()すること）。
    """
    for i, name in enumerate(names):
        conditions = [DisplayOrder.kind == kind, DisplayOrder.name == name]
        if faculty is not None:
            conditions.append(DisplayOrder.faculty == faculty)
        existing = (await session.execute(select(DisplayOrder).where(*conditions))).scalar_one_or_none()
        if existing:
            existing.sort_order = i
        else:
            kwargs = {"kind": kind, "name": name, "sort_order": i}
            if faculty is not None:
                kwargs["faculty"] = faculty
            session.add(DisplayOrder(**kwargs))
