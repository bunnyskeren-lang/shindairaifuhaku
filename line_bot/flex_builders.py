import asyncio

from linebot.v3.messaging import (
    FlexBox,
    FlexBubble,
    FlexButton,
    FlexMessage,
    FlexText,
    PostbackAction,
    URIAction,
)

from core import cache
from core.config import (
    CONTACT_URL, EASE_COLOR, EASE_LABEL, EASE_STARS, PRIVACY_URL, TERMS_URL,
    REGISTRATION_WELCOME_UNLOCK_CREDITS, REVIEW_APPROVAL_UNLOCK_CREDITS,
    REVIEW_SUBMISSION_CATEGORY, REVIEW_SUBMISSION_RESTRICTED_MESSAGE,
    make_course_liff_url, make_review_liff_url,
)
from models import Subject

# ── FlexMessage builder ─────────────────────────────────────────


def _build_course_flex(course: Subject, all_instrs: dict, all_stats: dict) -> FlexMessage:
    cached = cache.get_flex_cache(course.id)
    if cached is not None:
        return cached

    instructors = all_instrs.get(course.id, [])
    review_count, top_ease_flex = all_stats.get(course.name, (0, None))

    instructor_str = "・".join(i.name for i in instructors) or "未設定"
    liff_url = make_course_liff_url(course.id)

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


async def get_course_flex(course: Subject, user_id: str) -> FlexMessage:
    cached = cache.get_flex_cache(course.id)
    if cached is not None:
        return cached
    all_instrs, all_stats = await asyncio.gather(
        cache.get_all_instructors_cached(),
        cache.get_all_review_stats_cached(),
    )
    return _build_course_flex(course, all_instrs, all_stats)


async def prewarm_flex_cache() -> None:
    # 修正理由: 科目数分だけget_course_flexを直列awaitすると、直前に温めたキャッシュを
    # 科目ごとに毎回asyncio.gatherで引き直すだけの無駄が起きていたため、gatherはループの外で1回だけ行う
    _, all_courses = await cache.get_courses_cached()
    all_instrs, all_stats = await asyncio.gather(
        cache.get_all_instructors_cached(),
        cache.get_all_review_stats_cached(),
    )
    for course in all_courses:
        _build_course_flex(course, all_instrs, all_stats)


def make_no_review_flex(course: Subject, user_id: str = "") -> FlexMessage:
    liff_url = make_course_liff_url(course.id)
    can_submit = course.category == REVIEW_SUBMISSION_CATEGORY

    footer_buttons = []
    if can_submit:
        form_url = make_review_liff_url(course.name, user_id)
        footer_buttons.append(FlexButton(
            action=URIAction(label="✏️ レビューを投稿する", uri=form_url),
            style="primary", color="#6366f1", height="sm",
        ))
    footer_buttons.append(FlexButton(
        action=URIAction(label="📖 科目詳細を見る", uri=liff_url),
        style="secondary", height="sm",
    ))

    second_line = (
        "あなたが最初のレビュワーになりませんか？🌟" if can_submit
        else REVIEW_SUBMISSION_RESTRICTED_MESSAGE
    )

    return FlexMessage(
        alt_text=f"📖 {course.name}",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text=course.name, weight="bold", size="md", color="#ffffff", wrap=True),
                ],
                background_color="#6366f1",
                padding_all="lg",
            ),
            body=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text="まだレビューがありません 😢", weight="bold", size="sm", color="#475569"),
                    FlexText(
                        text=second_line,
                        size="xs", color="#64748b", margin="sm", wrap=True,
                    ),
                ],
                padding_all="lg",
            ),
            footer=FlexBox(
                layout="vertical",
                spacing="sm",
                padding_all="md",
                contents=footer_buttons,
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
                    card("✏️", "レビュー投稿", "レビュー投稿フォームを開く", bg="#f5f3ff"),
                    card("📖", "レビュー閲覧", "科目一覧からレビューをチェック（🎫チケットが必要）", bg="#f5f3ff"),
                    card("🏆", "楽単5選 / 鬼単5選", "楽単・鬼単科目ランキングTOP5を表示", bg="#f5f3ff"),
                    card("🎴", "10連おみくじ", "ランダムに10科目をおみくじ形式で紹介", bg="#f5f3ff"),
                    card("🔗", "外部サービス",
                         "うりぼーポータル・BEEF+・食堂メニュー・\n生協アプリ・市バス・図書館へ移動",
                         bg="#f5f3ff"),
                    section_label("🎫  レビュー閲覧チケット"),
                    card("🎫", "チケットとは",
                         "他の人のレビューを見るには科目ごとに🎫チケットを1枚使います",
                         bg="#fffbeb", icon_color="#d97706"),
                    card("🎁", "もらい方",
                         f"会員登録で{REGISTRATION_WELCOME_UNLOCK_CREDITS}枚、"
                         f"自分のレビューが承認されるたびに{REVIEW_APPROVAL_UNLOCK_CREDITS}枚もらえます",
                         bg="#fffbeb", icon_color="#d97706"),
                    section_label("💬  チャット"),
                    card("🔍", "科目名を送る",
                         "授業情報・レビューを表示\n例：「英語」「データサイエンス」",
                         bg="#eff6ff"),
                    card("🏆", "楽単 / 鬼単",
                         "「楽単」→ 楽単ランキング\n「鬼単」→ 鬼単ランキング",
                         bg="#eff6ff"),
                ],
                padding_all="lg",
                background_color="#fafafa",
            ),
            footer=FlexBox(
                layout="vertical",
                contents=[
                    FlexButton(
                        action=URIAction(label="📬 お問い合わせ", uri=CONTACT_URL),
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
    _notice_contents = [
        FlexText(
            text="⚠️ ご利用には会員登録が必要です",
            weight="bold",
            size="sm",
            color="#c2410c",
            wrap=True,
        ),
        FlexText(
            text="お名前・学籍番号・学部・学科を入力するだけ（30秒で完了）",
            size="xs",
            color="#9a3412",
            wrap=True,
            margin="sm",
        ),
    ]
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
                        contents=_notice_contents,
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
                    FlexText(
                        text="登録することで利用規約およびプライバシーポリシーに同意したものとします",
                        size="xxs",
                        color="#9ca3af",
                        wrap=True,
                        margin="md",
                    ),
                    FlexBox(
                        layout="horizontal",
                        margin="sm",
                        contents=[
                            FlexButton(
                                action=URIAction(label="利用規約", uri=TERMS_URL),
                                style="link",
                                height="sm",
                            ),
                            FlexButton(
                                action=URIAction(label="プライバシーポリシー", uri=PRIVACY_URL),
                                style="link",
                                height="sm",
                            ),
                        ],
                    ),
                ],
                padding_all="md",
            ),
        ),
    )


# 楽単5選/鬼単5選の順位バッジ配色（1位が最も強調される5段階のグラデーション）。
# インデックス0=1位。件数が5を超える場合は末尾の色を使い回す。
_RAKUTAN_RANK_COLORS = [
    ("#f59e0b", "#ffffff"),
    ("#fbbf24", "#ffffff"),
    ("#fcd34d", "#78350f"),
    ("#fde68a", "#78350f"),
    ("#fef3c7", "#92400e"),
]
_ONITAN_RANK_COLORS = [
    ("#7f1d1d", "#ffffff"),
    ("#b91c1c", "#ffffff"),
    ("#dc2626", "#ffffff"),
    ("#f87171", "#ffffff"),
    ("#fecaca", "#7f1d1d"),
]


def _make_ranking_card(
    title: str,
    items: list[dict],
    header_bg: str,
    row_bg: str,
    accent: str,
    subtitle: str = "",
    row_bg_top: str | None = None,
    rank_colors: list[tuple[str, str]] | None = None,
    top_emoji: str | None = None,
    footer_button_label: str | None = None,
    footer_button_uri: str | None = None,
) -> FlexMessage:
    # items: [{"rank": int, "id": int, "name": str, "stars": str, "ease": str}]
    # rankは1始まりの表示順位。easeはSS/S/A/B/C（EASE_LABEL/EASE_COLORのキー、無ければ""）
    rows = []
    for item in items:
        liff_url = make_course_liff_url(item["id"])
        pos = item["rank"]
        if rank_colors:
            badge_bg, badge_color = rank_colors[min(pos - 1, len(rank_colors) - 1)]
        else:
            badge_bg, badge_color = accent, "#ffffff"
        badge_text = top_emoji if (pos == 1 and top_emoji) else str(pos)
        tier_label = EASE_LABEL.get(item.get("ease", ""), "")
        tier_color = EASE_COLOR.get(item.get("ease", ""), accent)
        row_bg_color = row_bg_top if (pos == 1 and row_bg_top) else row_bg

        stars_row_contents = [FlexText(text=item["stars"], size="xs", color=tier_color, flex=0)]
        if tier_label:
            stars_row_contents.append(FlexText(text=tier_label, size="xxs", color="#94a3b8", flex=0, margin="xs"))

        rows.append(
            FlexBox(
                layout="horizontal",
                align_items="center",
                background_color=row_bg_color,
                corner_radius="12px",
                padding_all="md",
                margin="sm",
                action=URIAction(label=item["name"][:20], uri=liff_url),
                contents=[
                    FlexBox(
                        layout="vertical",
                        width="34px", height="34px", corner_radius="17px",
                        background_color=badge_bg,
                        justify_content="center", align_items="center",
                        contents=[FlexText(text=badge_text, color=badge_color, weight="bold", size="sm", align="center")],
                    ),
                    FlexBox(
                        layout="vertical",
                        flex=1,
                        margin="md",
                        contents=[
                            FlexText(text=item["name"], weight="bold", size="sm", color="#1e293b", wrap=True, max_lines=1),
                            FlexBox(layout="horizontal", margin="xs", contents=stars_row_contents),
                        ],
                    ),
                    FlexText(text="›", color="#cbd5e1", size="lg", weight="bold", flex=0, gravity="center"),
                ],
            )
        )

    header_contents = [FlexText(text=title, weight="bold", color="#ffffff", size="md")]
    if subtitle:
        header_contents.append(FlexText(text=subtitle, size="xxs", color="#ffffff", margin="xs"))

    footer_contents = [FlexText(text="タップすると科目の詳細が見られます", size="xxs", color="#94a3b8", align="center")]
    if footer_button_label and footer_button_uri:
        footer_contents.append(
            FlexButton(
                action=URIAction(label=footer_button_label, uri=footer_button_uri),
                style="primary", color=accent, height="sm", margin="sm",
            )
        )

    bubble = FlexBubble(
        header=FlexBox(layout="vertical", contents=header_contents, background_color=header_bg, padding_all="lg"),
        body=FlexBox(layout="vertical", contents=rows, padding_all="lg"),
        footer=FlexBox(layout="vertical", contents=footer_contents, padding_all="md"),
    )
    return FlexMessage(alt_text=title, contents=bubble)


def make_rakutan_card(items: list[dict]) -> FlexMessage:
    return _make_ranking_card(
        "😴 楽単5選", items, header_bg="#f59e0b", row_bg="#fffbeb", accent="#f59e0b",
        subtitle="承認済みレビューから厳選", row_bg_top="#fef3c7",
        rank_colors=_RAKUTAN_RANK_COLORS, top_emoji="👑",
        footer_button_label="📝 レビューを投稿", footer_button_uri=make_review_liff_url(),
    )


def make_onitan_card(items: list[dict]) -> FlexMessage:
    return _make_ranking_card(
        "👹 鬼単5選", items, header_bg="#b91c1c", row_bg="#fef2f2", accent="#b91c1c",
        subtitle="承認済みレビューから厳選", row_bg_top="#fee2e2",
        rank_colors=_ONITAN_RANK_COLORS, top_emoji="💀",
        footer_button_label="📝 レビューを投稿", footer_button_uri=make_review_liff_url(),
    )


def make_omikuji_card(items: list[dict]) -> FlexMessage:
    # items: [{"rank": int, "id": int, "name": str, "stars": str, "ease": str}]  楽単5選/鬼単5選と同じ表記。stars/easeは楽単度
    # 楽単5選/鬼単5選と同じ縦一列リストだと10件分でカードが縦に長くなりスマホ1画面に収まらないため、
    # 2列×5行のグリッドに圧縮した専用レイアウトにしている(2026-09-02)。
    # おみくじらしく楽単度ラベル(天国/楽々/標準/大変/修羅場)を主役にし、★は補助情報として下段に添える。
    header_bg, row_bg, accent = "#7c3aed", "#f5f3ff", "#7c3aed"

    def cell(item: dict) -> FlexBox:
        tier_label = EASE_LABEL.get(item.get("ease", ""), "")
        tier_color = EASE_COLOR.get(item.get("ease", ""), accent)
        detail_row = [FlexText(text=item["stars"], size="xxs", color="#94a3b8", flex=0)]
        if tier_label:
            detail_row.insert(0, FlexText(text=tier_label, size="xs", weight="bold", color=tier_color, flex=0))
        return FlexBox(
            layout="vertical",
            flex=1,
            background_color=row_bg,
            corner_radius="10px",
            padding_all="sm",
            action=URIAction(label=item["name"][:20], uri=make_course_liff_url(item["id"])),
            contents=[
                FlexBox(
                    layout="horizontal",
                    spacing="xs",
                    align_items="center",
                    contents=[
                        FlexBox(
                            layout="vertical",
                            width="20px", height="20px", corner_radius="10px",
                            background_color=accent, flex=0,
                            justify_content="center", align_items="center",
                            contents=[FlexText(text=str(item["rank"]), color="#ffffff", weight="bold", size="xxs", align="center")],
                        ),
                        FlexText(text=item["name"], weight="bold", size="xs", color="#1e293b", wrap=True, max_lines=1, flex=1),
                    ],
                ),
                FlexBox(layout="horizontal", spacing="xs", margin="xs", contents=detail_row),
            ],
        )

    # 母数(承認済みレビューがある科目)が10件未満だと items は10件より少なくなりうるため、
    # zip(items[0::2], items[1::2])のような組み方だと件数が奇数の場合に末尾の1件が黙って
    # 落ちる。range+スライスで組めば奇数件でも最後の行を1セルだけの行として拾える。
    rows = [
        FlexBox(layout="horizontal", spacing="sm", margin=("none" if i == 0 else "sm"), contents=[cell(x) for x in items[i:i + 2]])
        for i in range(0, len(items), 2)
    ]

    bubble = FlexBubble(
        header=FlexBox(
            layout="vertical",
            background_color=header_bg,
            padding_all="md",
            contents=[
                FlexText(text="⛩️ 10連おみくじ", weight="bold", color="#ffffff", size="md"),
                FlexText(text="科目をタップで詳細を表示", size="xxs", color="#ffffff", margin="xs"),
            ],
        ),
        body=FlexBox(layout="vertical", contents=rows, padding_all="md"),
        footer=FlexBox(
            layout="vertical",
            padding_all="md",
            contents=[
                FlexButton(
                    action=URIAction(label="📝 レビューを投稿", uri=make_review_liff_url()),
                    style="primary", color=accent, height="sm",
                ),
            ],
        ),
    )
    return FlexMessage(alt_text="⛩️ 10連おみくじ", contents=bubble)


def _make_search_result_row(item: dict) -> FlexBox:
    # 改行行数をできるだけ減らすため、学部名は科目名と同じ行に丸括弧で付記し、
    # 未レビュー時のレビュー投稿導線もボタンではなくテキストリンクにして高さを抑える
    stars_text = item["stars"] or "評価なし"
    stars_color = "#f59e0b" if item["stars"] else "#94a3b8"
    liff_url = make_course_liff_url(item['id'])
    name_text = item["name"]
    if item.get("faculty"):
        name_text = f"{name_text}（{item['faculty']}）"
    row_contents = [
        FlexBox(
            layout="horizontal",
            spacing="sm",
            contents=[
                FlexText(
                    text=name_text, weight="bold", size="sm", color="#1e293b",
                    wrap=True, flex=1,
                ),
                FlexText(text=stars_text, size="sm", color=stars_color, flex=0,
                          align="end", gravity="center"),
            ],
        ),
    ]
    if not item["stars"] and item.get("category") == REVIEW_SUBMISSION_CATEGORY:
        form_url = make_review_liff_url(item['name'])
        row_contents.append(
            FlexText(
                text="✏️ レビューを投稿する", size="xxs", color="#7c3aed", margin="xs",
                action=URIAction(label="レビュー投稿", uri=form_url),
            )
        )
    # 修正理由: 従来は科目名テキスト部分のみがタップ対象で、行の背景（紫の領域）を
    # 押しても反応しなかったため誤タップが起きやすかった。行全体（このコンテナ）に
    # actionを設定し、内側の「レビューを投稿する」テキストだけは独自actionが優先されて
    # レビュー投稿導線として機能する（LINE Flex Messageの仕様上、子要素のactionが親を上書きする）。
    return FlexBox(
        layout="vertical",
        background_color="#f5f3ff",
        corner_radius="10px",
        padding_all="sm",
        margin="xs",
        contents=row_contents,
        action=URIAction(label=item["name"][:20], uri=liff_url),
    )


def make_search_result_card(items: list[dict], title: str) -> FlexMessage:
    # items: [{"id": int, "name": str, "faculty": str, "stars": str}]  stars=""なら未レビュー
    bubble = FlexBubble(
        header=FlexBox(
            layout="vertical",
            contents=[FlexText(text=title, weight="bold", color="#ffffff", size="md", wrap=True)],
            background_color="#7c3aed",
            padding_all="lg",
        ),
        body=FlexBox(
            layout="vertical",
            contents=[_make_search_result_row(item) for item in items],
            padding_all="md",
            spacing="none",
        ),
        footer=FlexBox(
            layout="vertical",
            contents=[FlexText(text="科目名をタップすると詳細が見られます", size="xs", color="#94a3b8", align="center")],
            padding_all="md",
        ),
    )
    return FlexMessage(alt_text=title, contents=bubble)


# ── Course list carousel ────────────────────────────────────────


def make_category_select_flex() -> FlexMessage:
    categories = [
        ("📚 教養科目", "教養科目の系統を選んで表示します", "教養", "#6366f1", "#eef2ff", "#4f46e5"),
        ("🎓 専門科目", "学部を選んで専門科目を表示します",   "専門", "#0ea5e9", "#e0f2fe", "#0284c7"),
    ]
    btns = [
        FlexBox(
            layout="vertical",
            action=PostbackAction(label=label[:20], data=text),
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
    classifications: list[str] | list[tuple[str, str]],
    reviewed_cls: set | None = None,
    title: str = "📚 教養科目",
    subtitle: str = "系統を選んでください",
    header_color: str = "#6366f1",
    data_prefix: str = "",
    back_label: str | None = None,
    back_data: str | None = None,
) -> FlexMessage:
    """classifications は文字列のリスト、または (表示ラベル, postbackデータ) のタプルのリスト。
    タプルの場合、表示ラベルと実際に送信されるpostbackデータを分離できる
    （例：学科一覧で短い学科名を見せつつ、内部的には学部を含むfaculty値を送る）。
    back_label/back_dataを両方指定すると、一つ前の階層に戻るボタンをフッターに表示する
    （学部→学科→分類のような多階層ドリルダウンで迷子にならないようにするため）。"""
    if reviewed_cls is None:
        reviewed_cls = set()
    items = [(c, c) if isinstance(c, str) else c for c in classifications]
    btns = [
        FlexBox(
            layout="vertical",
            action=PostbackAction(label=label[:20], data=f"{data_prefix}{value}"),
            contents=[
                FlexText(
                    text=f"✓ {label}" if label in reviewed_cls else label,
                    size="lg",
                    color="#0f172a" if label in reviewed_cls else "#94a3b8",
                    weight="bold",
                    align="center",
                )
            ],
            background_color="#eef2ff" if label in reviewed_cls else "#f8fafc",
            border_width="2px",
            border_color="#0f172a" if label in reviewed_cls else "#cbd5e1",
            corner_radius="20px",
            padding_all="md",
        )
        for label, value in items
    ]
    footer_contents = []
    if reviewed_cls:
        footer_contents.append(
            FlexText(text="✓ = レビュー投稿済みを含む", size="xxs", color="#94a3b8", align="center")
        )
    if back_label and back_data:
        footer_contents.append(
            FlexButton(
                action=PostbackAction(label=back_label[:20], data=back_data),
                style="secondary",
                height="sm",
                margin="sm" if reviewed_cls else "none",
            )
        )
    footer = FlexBox(layout="vertical", contents=footer_contents, padding_all="md") if footer_contents else None
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
            footer=footer,
        ),
    )
