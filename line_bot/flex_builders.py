import asyncio
from urllib.parse import quote as _url_quote

from linebot.v3.messaging import (
    FlexBox,
    FlexBubble,
    FlexButton,
    FlexMessage,
    FlexSeparator,
    FlexText,
    MessageAction,
    PostbackAction,
    URIAction,
)

from core import cache
from core.config import APP_URL, CONTACT_EMAIL, EASE_STARS, PRIVACY_URL, REVIEW_FORM_URL
from models import Subject

# ── FlexMessage builder ─────────────────────────────────────────


async def get_course_flex(course: Subject, user_id: str) -> FlexMessage:
    cached = cache.get_flex_cache(course.id)
    if cached is not None:
        return cached

    all_instrs, all_stats = await asyncio.gather(
        cache.get_all_instructors_cached(),
        cache.get_all_review_stats_cached(),
    )
    instructors = all_instrs.get(course.id, [])
    review_count, top_ease_flex = all_stats.get(course.name, (0, None))

    instructor_str = "・".join(i.name for i in instructors) or "未設定"
    liff_url = f"{APP_URL}/liff/course?course_id={course.id}"

    meta_parts = []
    if getattr(course, "term_type", None):
        meta_parts.append(course.term_type)
    if getattr(course, "credits", None):
        meta_parts.append(f"{course.credits}単位")
    if course.classification:
        meta_parts.append(course.classification)

    header_contents = [
        FlexText(text=course.name, weight="bold", size="lg", color="#ffffff", wrap=True),
        FlexText(text=instructor_str, size="xs", color="#c7d2fe", margin="xs"),
    ]
    if meta_parts:
        header_contents.append(
            FlexText(text="  ".join(meta_parts), size="xxs", color="#a5b4fc", margin="xs")
        )

    body_contents = []
    if top_ease_flex is not None:
        body_contents.append(
            FlexBox(
                layout="horizontal",
                contents=[
                    FlexText(text="楽単度", size="xs", color="#94a3b8", flex=0),
                    FlexText(text=EASE_STARS.get(top_ease_flex, ""), size="xl", color="#FFD700", flex=0, margin="sm"),
                    FlexText(text=f"({review_count}件)", size="xs", color="#94a3b8", margin="sm"),
                ],
                align_items="center",
            )
        )
    elif review_count == 0:
        body_contents.append(
            FlexText(text="まだレビューはありません", size="sm", color="#94a3b8")
        )
    body_contents.append(
        FlexText(text="タップして詳細・レビューを確認", size="xs", color="#b0bec5", margin="md")
    )

    bubble = FlexBubble(
        header=FlexBox(layout="vertical", contents=header_contents,
                       background_color="#4f46e5", padding_all="lg"),
        body=FlexBox(layout="vertical", contents=body_contents, padding_all="lg"),
        footer=FlexBox(layout="vertical", contents=[
            FlexButton(action=URIAction(label="詳細・レビューを見る", uri=liff_url),
                       style="primary", color="#4f46e5", height="sm")
        ], padding_all="md"),
    )
    msg = FlexMessage(alt_text=f"📖 {course.name}", contents=bubble)
    cache.set_flex_cache(course.id, msg)
    return msg


async def prewarm_flex_cache() -> None:
    _, all_courses = await cache.get_courses_cached()
    for course in all_courses:
        await get_course_flex(course, "")


def make_no_review_flex(course: Subject, user_id: str = "") -> FlexMessage:
    form_url = f"{REVIEW_FORM_URL}?course={_url_quote(course.name)}"
    if user_id:
        form_url += f"&uid={user_id}"
    liff_url = f"{APP_URL}/liff/course?course_id={course.id}"
    return FlexMessage(
        alt_text=f"📖 {course.name}",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text=course.name, weight="bold", size="md", color="#ffffff", wrap=True),
                ],
                background_color="#94a3b8",
                padding_all="lg",
            ),
            body=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text="まだレビューがありません 😢", weight="bold", size="sm", color="#475569"),
                    FlexText(
                        text="あなたが最初のレビュワーになりませんか？🌟",
                        size="xs", color="#64748b", margin="sm", wrap=True,
                    ),
                ],
                padding_all="lg",
            ),
            footer=FlexBox(
                layout="vertical",
                spacing="sm",
                padding_all="md",
                contents=[
                    FlexButton(
                        action=URIAction(label="✏️ レビューを投稿する", uri=form_url),
                        style="primary", color="#6366f1", height="sm",
                    ),
                    FlexButton(
                        action=URIAction(label="📖 科目詳細を見る", uri=liff_url),
                        style="secondary", height="sm",
                    ),
                ],
            ),
        ),
    )


# ── Static reply texts ──────────────────────────────────────────


def make_help_flex() -> FlexMessage:
    def section_label(text: str) -> FlexText:
        return FlexText(text=text, size="xxs", weight="bold", color="#6366f1",
                        margin="lg")

    def card(icon: str, title: str, desc: str, bg: str = "#f5f3ff", icon_color: str = "#6366f1") -> FlexBox:
        return FlexBox(
            layout="horizontal",
            background_color=bg,
            corner_radius="10px",
            padding_all="md",
            margin="sm",
            contents=[
                FlexBox(
                    layout="vertical",
                    contents=[FlexText(text=icon, size="lg", align="center", gravity="center")],
                    width="36px",
                    height="36px",
                    background_color="#ffffff",
                    corner_radius="8px",
                    flex=0,
                    justify_content="center",
                    align_items="center",
                ),
                FlexBox(
                    layout="vertical",
                    flex=1,
                    margin="md",
                    contents=[
                        FlexText(text=title, weight="bold", size="sm", color="#1e1b4b"),
                        FlexText(text=desc, size="xs", color="#6b7280", wrap=True, margin="xs"),
                    ],
                ),
            ],
        )

    return FlexMessage(
        alt_text="神大ライフハック 使い方ガイド",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexBox(
                        layout="horizontal",
                        contents=[
                            FlexText(text="🎓", size="xxl", flex=0),
                            FlexBox(
                                layout="vertical",
                                flex=1,
                                margin="md",
                                contents=[
                                    FlexText(text="神大ライフハック", weight="bold",
                                             color="#ffffff", size="lg"),
                                    FlexText(text="使い方ガイド", color="#c7d2fe", size="xs"),
                                ],
                            ),
                        ],
                    ),
                ],
                background_color="#4f46e5",
                padding_all="xl",
            ),
            body=FlexBox(
                layout="vertical",
                contents=[
                    section_label("📱  リッチメニュー"),
                    card("📚", "教養", "教養科目を分類別に一覧表示", bg="#f5f3ff"),
                    card("✏️", "レビュー投稿", "レビュー投稿フォームを開く", bg="#f5f3ff"),
                    section_label("💬  チャット"),
                    card("🔍", "科目名を送る",
                         "授業情報・レビューを表示\n例：「英語」「データサイエンス」",
                         bg="#eff6ff"),
                    card("🏆", "人気 / 楽単",
                         "「人気」→ 高評価 TOP5\n「楽単」→ 楽単 TOP5",
                         bg="#eff6ff"),
                ],
                padding_all="lg",
                background_color="#fafafa",
            ),
            footer=FlexBox(
                layout="vertical",
                contents=[
                    FlexButton(
                        action=URIAction(label="📬 お問い合わせ", uri=f"mailto:{CONTACT_EMAIL}"),
                        style="primary",
                        color="#6366f1",
                        height="sm",
                    ),
                    FlexButton(
                        action=URIAction(label="プライバシーポリシー", uri=PRIVACY_URL),
                        style="link",
                        height="sm",
                    ),
                ],
                padding_all="md",
                spacing="sm",
            ),
        ),
    )


def make_registration_flex(register_url: str) -> FlexMessage:
    return FlexMessage(
        alt_text="🎓 神大ライフハックへようこそ！会員登録をお願いします",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text="🎓 神大ライフハックへ", weight="bold", color="#ffffff", size="xl"),
                    FlexText(text="ようこそ！", color="#c7d2fe", size="lg", weight="bold"),
                ],
                background_color="#6366f1",
                padding_all="xl",
            ),
            body=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(
                        text="先輩のリアルなレビューで\n授業選びをサポートします📖",
                        wrap=True,
                        size="sm",
                        color="#374151",
                    ),
                    FlexBox(
                        layout="vertical",
                        margin="lg",
                        padding_all="md",
                        background_color="#fff7ed",
                        corner_radius="md",
                        contents=[
                            FlexText(
                                text="⚠️ ご利用には会員登録が必要です",
                                weight="bold",
                                size="sm",
                                color="#c2410c",
                                wrap=True,
                            ),
                            FlexText(
                                text="お名前・学籍番号・学部・学年・学科を入力するだけ（30秒で完了）",
                                size="xs",
                                color="#9a3412",
                                wrap=True,
                                margin="sm",
                            ),
                        ],
                    ),
                ],
                padding_all="lg",
            ),
            footer=FlexBox(
                layout="vertical",
                contents=[
                    FlexButton(
                        action=URIAction(label="📝 今すぐ登録する（30秒）", uri=register_url),
                        style="primary",
                        color="#f97316",
                        height="md",
                    ),
                ],
                padding_all="md",
            ),
        ),
    )


# ── Ranking bubble ──────────────────────────────────────────────

RANK_MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}


def make_ranking_bubble(title: str, items: list[dict]) -> FlexBubble:
    # items: [{"rank": int, "name": str, "stars": str}]
    body_contents = []
    for i, item in enumerate(items):
        if i > 0:
            body_contents.append(FlexSeparator(margin="sm"))
        medal = RANK_MEDAL.get(item["rank"], f"{item['rank']}位")
        body_contents.append(
            FlexBox(
                layout="horizontal",
                contents=[
                    FlexText(text=medal, size="sm", flex=1, align="center", gravity="center"),
                    FlexBox(
                        layout="vertical",
                        contents=[
                            FlexText(
                                text=item["name"],
                                size="sm",
                                wrap=True,
                                weight="bold",
                                color="#1e293b",
                                action=MessageAction(
                                    label=item["name"][:20],
                                    text=item["name"],
                                ),
                            ),
                            FlexText(text=item["stars"], size="xs", color="#f59e0b", margin="xs"),
                        ],
                        flex=5,
                    ),
                ],
                spacing="md",
                margin="sm",
            )
        )
    return FlexBubble(
        header=FlexBox(
            layout="vertical",
            contents=[FlexText(text=title, weight="bold", color="#ffffff", size="md")],
            background_color="#6366f1",
            padding_all="lg",
        ),
        body=FlexBox(layout="vertical", contents=body_contents, padding_all="lg"),
        footer=FlexBox(
            layout="vertical",
            contents=[FlexText(text="科目名をタップすると詳細が見られます", size="xs", color="#94a3b8", align="center")],
            padding_all="md",
        ),
    )


# ── Course list carousel ────────────────────────────────────────


def _variant_suffix(base: str, full: str) -> str:
    if full.startswith(base) and len(full) > len(base):
        return full[len(base):]
    for b_ch, f_ch in zip(base, full):
        if b_ch != f_ch:
            return f_ch
    return full[-1] if full else ""


def make_variant_selection_bubble(base_name: str, variant_names: list[str], reviewed_names: set[str] = frozenset()) -> FlexMessage:
    suffix_str = " / ".join(_variant_suffix(base_name, n) for n in variant_names)
    rows = []
    for name in variant_names:
        color = "#4f46e5" if name in reviewed_names else "#94a3b8"
        rows.append(
            FlexBox(
                layout="vertical",
                action=PostbackAction(label=name[:20], data=name),
                contents=[FlexText(text=name, wrap=True, size="sm", color=color)],
                padding_top="sm",
                padding_bottom="sm",
            )
        )
    return FlexMessage(
        alt_text=f"📚 {base_name} — {suffix_str} どれを見ますか？",
        contents=FlexBubble(
            size="kilo",
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text=base_name, weight="bold", color="#ffffff", size="sm", wrap=True),
                ],
                background_color="#6366f1",
                padding_all="md",
            ),
            body=FlexBox(
                layout="vertical",
                contents=rows,
                spacing="xs",
                padding_all="md",
            ),
        ),
    )


def make_category_select_flex() -> FlexMessage:
    categories = [
        ("📚 教養科目", "教養科目の系統を選んで表示します", "教養", "#6366f1", "#eef2ff", "#4f46e5"),
        ("🎓 専門科目", "経営学部の専門科目を表示します",   "専門", "#0ea5e9", "#e0f2fe", "#0284c7"),
    ]
    btns = [
        FlexBox(
            layout="vertical",
            action=MessageAction(label=label[:20], text=text),
            contents=[
                FlexText(text=label, size="lg", color=fg, weight="bold", align="center"),
                FlexText(text=desc,  size="xs", color="#64748b", align="center", wrap=True),
            ],
            background_color=bg,
            border_width="2px",
            border_color=border,
            corner_radius="20px",
            padding_all="md",
        )
        for label, desc, text, fg, bg, border in categories
    ]
    return FlexMessage(
        alt_text="📚 科目一覧 — カテゴリを選んでください",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text="📚 科目一覧", weight="bold", color="#ffffff", size="lg"),
                    FlexText(text="カテゴリを選んでください", color="#c7d2fe", size="sm"),
                ],
                background_color="#6366f1",
                padding_all="lg",
            ),
            body=FlexBox(
                layout="vertical",
                contents=btns,
                spacing="sm",
                padding_all="md",
            ),
        ),
    )


def make_classification_select_flex(
    classifications: list[str],
    reviewed_cls: set | None = None,
    title: str = "📚 教養科目",
    subtitle: str = "系統を選んでください",
    header_color: str = "#6366f1",
    data_prefix: str = "",
) -> FlexMessage:
    if reviewed_cls is None:
        reviewed_cls = set()
    btns = [
        FlexBox(
            layout="vertical",
            action=PostbackAction(label=cls[:20], data=f"{data_prefix}{cls}"),
            contents=[
                FlexText(
                    text=cls,
                    size="lg",
                    color="#0f172a" if cls in reviewed_cls else "#94a3b8",
                    weight="bold",
                    align="center",
                )
            ],
            background_color="#eef2ff" if cls in reviewed_cls else "#f8fafc",
            border_width="2px",
            border_color="#0f172a" if cls in reviewed_cls else "#cbd5e1",
            corner_radius="20px",
            padding_all="md",
        )
        for cls in classifications
    ]
    return FlexMessage(
        alt_text=f"{title} — {subtitle}",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text=title, weight="bold", color="#ffffff", size="lg"),
                    FlexText(text=subtitle, color="#c7d2fe", size="sm"),
                ],
                background_color=header_color,
                padding_all="lg",
            ),
            body=FlexBox(
                layout="vertical",
                contents=btns,
                spacing="sm",
                padding_all="md",
            ),
        ),
    )
