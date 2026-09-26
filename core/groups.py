"""団体（サークル等）経由のレビュー収集（2026-09-26、docs/BUSINESS_STRATEGY.md 3.3）。

契約した団体に「団体コード」を渡し、会員がレビュー投稿フォームで入力する。団体には
- 承認済みレビュー1件ごとに教養50円・専門30円
- 承認済みレビューが1件以上ある学籍番号の人数が10人に達するごとに+500円
を支払う（単価は core/config.py の GROUP_* 定数）。個人へは従来どおりチケットのみ。
数えるのは「団体コードを入力して投稿したレビュー」だけ（`reviews.group_id`は投稿時に番号を入力した場合のみ入る。
所属済みでも番号を入力しなかった投稿は数えない）。

このモジュールは「番号の生成・正規化・照合」「所属の固定ルール」「支払額の集計」だけを持つ。
支払いの名目（協賛金/成果報酬）や団体への案内文は事務上の未決事項なので、ここでは決めない。
"""
import secrets
import unicodedata

from sqlalchemy import distinct, func, select

from core.config import (
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


async def locked_group_id(session, profile: UserProfile) -> int | None:
    """この会員の現在の所属団体id（無ければNone）。フォームの団体コード欄の入力済み表示に使う。

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


async def group_stats(session) -> dict[int, dict]:
    """団体ごとの集計（group_id → 内訳dict＋精算済み額・未精算残高・所属会員数）。個人は特定しない。

    対象は status='approved' のレビューのみ（承認後に却下・差し戻しされたものは自動で外れる）。
    別分類への複製レビュー（copied_from_review_id）は元の投稿の二重計上になるので除く。
    """
    approved = (
        Review.group_id.is_not(None),
        Review.status == ReviewStatus.APPROVED,
        Review.copied_from_review_id.is_(None),
    )
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
        info = group_payout_breakdown(c.get("教養", 0), c.get("専門", 0), contributors.get(gid, 0))
        info["paid"] = int(paid.get(gid, 0))
        info["balance"] = info["accrued"] - info["paid"]
        info["member_count"] = members.get(gid, 0)
        stats[gid] = info
    return stats
