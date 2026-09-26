"""admin系ルーター間で共有する並び替え(up/down)処理のヘルパー。

instructor/faculty/course/classification の各「上へ/下へ」エンドポイントが、
対象を1つ隣にスワップして並び順を振り直すという同一パターンをそれぞれ
個別実装していたため、ここに集約する。
"""
from fastapi import Request
from sqlalchemy import select

from core.config import CHANNEL, CHANNEL_GUEST, CHANNEL_MAIN
from models import DisplayOrder

# 管理画面のチャンネル切替（ゲスト用bot / 本番bot / 両方）。ログ系の source 列で絞り込む。
# 選択はブラウザのCookie（templates/admin/base.html のトグルがJSで設定）に保持し、
# 未選択のときは「このサービス自身のチャンネル」（本番の管理画面なら main、ゲスト用なら guest）。
CHANNEL_ALL = "all"
ADMIN_CHANNEL_COOKIE = "admin_channel"
_CHANNEL_CHOICES = (CHANNEL_MAIN, CHANNEL_GUEST, CHANNEL_ALL)


async def admin_channel(request: Request) -> str:
    """表示対象チャンネル（main / guest / all）を決めて返すDepends用関数。
    `?channel=` クエリ > Cookie > このサービスのCHANNEL の優先順。
    テンプレートのトグル表示用に request.state.channel にも入れる。"""
    value = request.query_params.get("channel") or request.cookies.get(ADMIN_CHANNEL_COOKIE)
    if value not in _CHANNEL_CHOICES:
        value = CHANNEL
    request.state.channel = value
    request.state.default_channel = CHANNEL
    return value


def channel_conds(column, channel: str) -> list:
    """`.where(*channel_conds(Model.source, ch))` 用。allなら絞り込まない。"""
    return [] if channel == CHANNEL_ALL else [column == channel]


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
