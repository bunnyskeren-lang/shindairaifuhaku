"""団体（サークル等）経由のレビュー収集（2026-09-26、docs/BUSINESS_STRATEGY.md 3.3）。

契約した団体に「団体コード」を渡し、会員がレビュー投稿フォームで入力する。団体には
- 承認済みレビュー1件ごとに教養50円・専門30円
- 承認済みレビューが1件以上ある学籍番号の人数が10人に達するごとに+500円
を支払う（単価は core/config.py の GROUP_* 定数）。個人へは従来どおりチケットのみ。
数えるのは「団体コードを入力して投稿したレビュー」だけ（`reviews.group_id`は投稿時に番号を入力した場合のみ入る。
所属済みでも番号を入力しなかった投稿は数えない）。

このモジュールは「番号の生成・正規化・照合」「所属の管理（投稿の都度、入力した団体コードへ更新。固定ではない）」
「支払額の集計」だけを持つ。
支払いの名目は成果報酬（紹介・広報協力の対価。覚書で定める）に決定済み（2026-09-27）。団体への案内文は
事務上の未決事項なので、ここでは決めない。
"""
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta

from sqlalchemy import distinct, func, select

from core.config import (
    GROUP_BANK_FEE,
    GROUP_CONTRIBUTOR_BONUS_AMOUNT,
    GROUP_CONTRIBUTOR_BONUS_UNIT,
    GROUP_REVIEW_PAYOUT_KYOYO,
    GROUP_REVIEW_PAYOUT_SENMON,
)
from models import CourseSection, Group, GroupPayout, Review, ReviewStatus, Subject, UserProfile

# 紛らわしい文字（0とO、1とIとL）を除いた英大文字+数字
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8  # 31^8 ≒ 8.5e11。総当たりはレート制限と合わせて実質不可能
_MAX_INPUT_LEN = 32

GROUP_CODE_NOT_FOUND_MESSAGE = "団体コードが無効です（該当する団体がありません。コードをお確かめください）"
GROUP_CODE_INACTIVE_MESSAGE = "団体コードが無効です（この団体は現在停止中です）"


def generate_group_code() -> str:
    """団体コードを1つ発行する（暗号論的乱数）。重複チェックは呼び出し側で行う。"""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_group_code(raw: str | None) -> str:
    """入力された団体コードを照合用に整える（全角→半角・大文字化・前後の空白除去）。"""
    return unicodedata.normalize("NFKC", raw or "").strip().upper()[:_MAX_INPUT_LEN]


async def find_group_by_code(session, raw_code: str | None) -> Group | None:
    """団体コードから団体を探す（有効・無効を問わない）。空・見つからなければNone。"""
    code = normalize_group_code(raw_code)
    if not code:
        return None
    return (await session.execute(select(Group).where(Group.code == code))).scalar_one_or_none()


async def current_group_id(session, profile: UserProfile) -> int | None:
    """この会員の現在の所属団体id（無ければNone）。フォームの団体コード欄の入力済み表示に使う。
    （所属は固定ではなく、別の団体コードで投稿すれば書き換わる。2026-09-27に名前を実態へ合わせた）

    自分のプロフィールに加え、同じ学籍番号の別プロフィール（別のLINEアカウントで登録した場合）も見る。
    所属は投稿時に別の団体コードを入力すれば書き換わる（書き換え前のレビューは投稿時点の団体のまま）。
    """
    if profile.group_id is not None:
        return profile.group_id
    return (await session.execute(
        select(UserProfile.group_id)
        .where(UserProfile.student_id == profile.student_id, UserProfile.group_id.is_not(None))
        .order_by(UserProfile.created_at.asc())
        .limit(1)
    )).scalar_one_or_none()


# ── 支払額の計算 ───────────────────────────────────────────────────────────

def group_payout_breakdown(kyoyo_count: int, senmon_count: int, contributor_count: int) -> dict:
    """承認済みレビュー件数（教養/専門）と投稿人数から、団体への発生額と内訳を返す。

    人数ボーナスは「承認済みが1件以上ある学籍番号の人数」が10人に達するごと（9人→0、10人→500、
    19人→500、20人→1,000）。団体ごとの上限は設けない。
    """
    kyoyo_amount = kyoyo_count * GROUP_REVIEW_PAYOUT_KYOYO
    senmon_amount = senmon_count * GROUP_REVIEW_PAYOUT_SENMON
    bonus_blocks = contributor_count // GROUP_CONTRIBUTOR_BONUS_UNIT
    bonus_amount = bonus_blocks * GROUP_CONTRIBUTOR_BONUS_AMOUNT
    return {
        "kyoyo_count": kyoyo_count,
        "senmon_count": senmon_count,
        "contributor_count": contributor_count,
        "kyoyo_amount": kyoyo_amount,
        "senmon_amount": senmon_amount,
        "bonus_blocks": bonus_blocks,
        "bonus_amount": bonus_amount,
        "accrued": kyoyo_amount + senmon_amount + bonus_amount,
    }


def _approved_conditions() -> tuple:
    """団体の集計対象になるレビューの条件（団体経由・承認済み・複製でない）。"""
    return (
        Review.group_id.is_not(None),
        Review.status == ReviewStatus.APPROVED,
        Review.copied_from_review_id.is_(None),
    )


async def group_stats(session) -> dict[int, dict]:
    """団体ごとの集計（group_id → 内訳dict＋精算済み額・未精算残高・所属会員数）。個人は特定しない。

    対象は status='approved' のレビューのみ（承認後に却下・差し戻しされたものは自動で外れる）。
    別分類への複製レビュー（copied_from_review_id）は元の投稿の二重計上になるので除く。
    """
    approved = _approved_conditions()
    by_category = (await session.execute(
        select(Review.group_id, Subject.category, func.count(Review.id))
        .join(CourseSection, CourseSection.id == Review.course_section_id)
        .join(Subject, Subject.id == CourseSection.subject_id)
        .where(*approved)
        .group_by(Review.group_id, Subject.category)
    )).all()
    contributors = dict((await session.execute(
        select(Review.group_id, func.count(distinct(Review.student_id)))
        .where(*approved, Review.student_id.is_not(None))
        .group_by(Review.group_id)
    )).all())
    paid = dict((await session.execute(
        select(GroupPayout.group_id, func.coalesce(func.sum(GroupPayout.amount), 0))
        .group_by(GroupPayout.group_id)
    )).all())
    members = dict((await session.execute(
        select(UserProfile.group_id, func.count(UserProfile.line_user_id))
        .where(UserProfile.group_id.is_not(None))
        .group_by(UserProfile.group_id)
    )).all())

    counts: dict[int, dict[str, int]] = {}
    for gid, category, n in by_category:
        counts.setdefault(gid, {})[(category or "").strip()] = n

    stats: dict[int, dict] = {}
    for gid in set(counts) | set(contributors) | set(paid) | set(members):
        c = counts.get(gid, {})
        stats[gid] = _with_settlement(
            group_payout_breakdown(c.get("教養", 0), c.get("専門", 0), contributors.get(gid, 0)),
            paid=int(paid.get(gid, 0)), member_count=members.get(gid, 0),
        )
    return stats


def _with_settlement(info: dict, paid: int, member_count: int) -> dict:
    """発生額の内訳に、精算済み額・未精算残高・所属会員数と、振込額を足す。

    振込手数料GROUP_BANK_FEEは運営負担（団体からは差し引かない、2026-09-27に団体負担から再変更）。
    振込額は未精算残高そのまま。bank_feeは運営側のコスト把握用に残す。
    最低振込額と繰り越しは運営の手作業で、ここでは強制しない。
    """
    info["paid"] = paid
    info["balance"] = info["accrued"] - paid
    info["member_count"] = member_count
    info["bank_fee"] = GROUP_BANK_FEE
    info["transfer_amount"] = info["balance"]
    return info


def empty_group_stats() -> dict:
    """まだ何も無い団体の集計（group_stats()の各値と同じキー構成）。"""
    return _with_settlement(group_payout_breakdown(0, 0, 0), paid=0, member_count=0)


# ── LINE botの「団体」（団体の成果の確認）────────────────────────────────

_MAX_GRADE = 8  # 医学部6年＋留年の余裕。これを超える（＝入学年度として不自然な）学籍番号は学年を出さない


def grade_label(student_id: str | None, now: datetime | None = None) -> str:
    """学籍番号の上2桁（入学年度の下2桁）から「N回生」を返す。読み取れなければ空文字。

    年度は4月始まり（JST）。2026年度（2026/4〜2027/3）に上2桁が26なら1回生。
    """
    head = (student_id or "")[:2]
    if len(head) != 2 or not head.isdigit():
        return ""
    jst = (now or datetime.now(UTC)) + timedelta(hours=9)
    academic_year = jst.year if jst.month >= 4 else jst.year - 1
    grade = academic_year - (2000 + int(head)) + 1
    return f"{grade}回生" if 1 <= grade <= _MAX_GRADE else ""


async def group_of_user(session, line_user_id: str) -> Group | None:
    """この会員が所属する有効な団体（無ければNone）。"""
    if not line_user_id:
        return None
    group_id = (await session.execute(
        select(UserProfile.group_id).where(UserProfile.line_user_id == line_user_id)
    )).scalar_one_or_none()
    if group_id is None:
        return None
    group = await session.get(Group, group_id)
    return group if group and group.is_active else None


async def group_member_data(session, group_id: int) -> tuple[dict[str, int], dict[str, str]]:
    """団体の承認済みレビュー件数(student_id→件数)と氏名(student_id→氏名)。
    group_report()とcore.cache.get_group_member_data_cached()の両方が使う共通クエリ。"""
    per_student = (await session.execute(
        select(Review.student_id, func.count(Review.id))
        .where(*_approved_conditions(), Review.group_id == group_id, Review.student_id.is_not(None))
        .group_by(Review.student_id)
    )).all()
    counts = {sid: n for sid, n in per_student}
    names: dict[str, str] = {}
    if counts:
        names = dict((await session.execute(
            select(UserProfile.student_id, func.min(UserProfile.name))
            .where(UserProfile.student_id.in_(list(counts)))
            .group_by(UserProfile.student_id)
        )).all())
    return counts, names


async def group_report(
    session, group: Group, line_user_id: str,
    *, stats: dict[int, dict] | None = None, member_data: tuple[dict[str, int], dict[str, str]] | None = None,
) -> dict:
    """団体の成果を返す。全会員向けは団体全体の集計と本人の件数、団体の管理者にだけ、
    承認済みレビューを投稿した会員の氏名と件数の一覧（members）を付ける。

    氏名は`user_profiles.name`（同じ学籍番号に複数のプロフィールがあれば先頭）、学年は学籍番号の上2桁から求める（`grade_label`）。学籍番号そのもの・学部は返さない。
    件数の対象は支払額と同じ（団体コードを入力して投稿した承認済みレビュー）。

    stats/member_data を渡すとgroup_stats()/group_member_data()の呼び出しを省いてそれを使う
    （LINE botの「団体」タップ経路 group_report_for_user() が core.cache のTTLキャッシュ値を渡す。
    Render⇄Supabase間のラウンドトリップが重く、タップのたびに素で叩くと2〜8秒かかっていたため
    2026-09-28に導入。省略時は常にsession上で最新値を取得する＝管理画面等、都度最新が必要な
    呼び出し元向けのデフォルト）。
    """
    if stats is None:
        stats = await group_stats(session)
    info = dict(stats.get(group.id) or group_payout_breakdown(0, 0, 0))
    info["is_manager"] = bool(group.manager_line_user_id) and group.manager_line_user_id == line_user_id
    info["bonus_remaining"] = GROUP_CONTRIBUTOR_BONUS_UNIT - info["contributor_count"] % GROUP_CONTRIBUTOR_BONUS_UNIT

    my_student_id = (await session.execute(
        select(UserProfile.student_id).where(UserProfile.line_user_id == line_user_id)
    )).scalar_one_or_none()
    if member_data is None:
        member_data = await group_member_data(session, group.id)
    counts, names = member_data
    info["my_count"] = counts.get(my_student_id, 0)

    info["members"] = []
    if info["is_manager"] and counts:
        info["members"] = sorted(
            ((names.get(sid) or "（氏名不明）", grade_label(sid), n) for sid, n in counts.items()),
            key=lambda x: (-x[2], x[0]),
        )
    return info


async def group_report_for_user(line_user_id: str) -> tuple[str, dict] | None:
    """LINE botの「団体」用。所属団体の(団体名, group_report()の結果)を返す。所属団体が無ければNone。

    団体全体の集計・会員別件数は core.cache 経由でTTLキャッシュされたものを渡す
    （group_report()のdocstring参照）。"""
    from database import AsyncSessionLocal  # モジュールimport時のDB接続を避けるため関数内で読む
    from core import cache  # 循環import回避のため遅延import

    async with AsyncSessionLocal() as session:
        group = await group_of_user(session, line_user_id)
        if group is None:
            return None
        stats = await cache.get_group_stats_cached()
        member_data = await cache.get_group_member_data_cached(group.id)
        return group.name, await group_report(session, group, line_user_id, stats=stats, member_data=member_data)
