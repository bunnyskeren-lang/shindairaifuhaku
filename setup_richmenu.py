"""
リッチメニューをセットアップするスクリプト。

実行:
  dev:  python setup_richmenu.py --env dev
  本番: python setup_richmenu.py --env prod  ← 確認プロンプトあり

必要な環境変数ファイル:
  dev:  programing files/.env.dev
  本番: programing files/.env
"""
import argparse
import io
import os
import sys
import urllib.request

parser = argparse.ArgumentParser(description="リッチメニューセットアップ")
parser.add_argument("--env", choices=["dev", "prod"], required=True,
                    help="実行環境: dev または prod")
parser.add_argument("image", nargs="?", help="カスタム画像パス（省略可）")
_args = parser.parse_args()

from dotenv import load_dotenv
if _args.env == "dev":
    load_dotenv("programing files/.env.dev")
    _default_url = "https://shindairaifuhaku-1.onrender.com"
else:
    load_dotenv("programing files/.env")
    _default_url = "https://shindairaifuhaku.onrender.com"

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    RichMenuArea,
    RichMenuBounds,
    RichMenuRequest,
    RichMenuSize,
    MessageAction,
    URIAction,
)

CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
REVIEW_FORM_URL = os.environ.get("REVIEW_FORM_URL", _default_url)

# リッチメニューサイズ (4列×2行)
W, H = 2500, 843
COLS, ROWS = 4, 2
CW, RH = W // COLS, H // ROWS  # 625 x 421

# セルに貼り込む画像 (ボタンインデックス → ファイルパス)
CELL_IMAGES = {
    6: "picture/uribo portal.png",
    7: "picture/BEEFplus.png",
}

BUTTONS = [
    # 上段
    {
        "label": "教養科目一覧",
        "color": "#0ea5e9",
        "action": MessageAction(label="教養科目一覧", text="教養"),
    },
    {
        "label": "Coming Soon",
        "color": "#94a3b8",
        "action": MessageAction(label="専門科目一覧", text="専門comingsoon"),
    },
    {
        "label": "レビュー投稿",
        "color": "#16a34a",
        "action": MessageAction(label="レビュー投稿", text="レビュー投稿"),
    },
    {
        "label": "ヘルプ",
        "color": "#475569",
        "action": MessageAction(label="ヘルプ", text="ヘルプ"),
    },
    # 下段
    {
        "label": "人気の授業",
        "color": "#ea580c",
        "action": MessageAction(label="人気の授業", text="人気の授業"),
    },
    {
        "label": "楽単ランキング",
        "color": "#ca8a04",
        "action": MessageAction(label="楽単ランキング", text="楽単ランキング"),
    },
    {
        "label": "うりぼーポータル",
        "color": "#0f766e",
        "action": URIAction(label="うりぼーポータル", uri="https://www.uriboportal.ofc.kobe-u.ac.jp/"),
    },
    {
        "label": "BEEFplus",
        "color": "#7c3aed",
        "action": URIAction(label="BEEFplus", uri="https://beefplus.center.kobe-u.ac.jp/login"),
    },
]


def load_font(size: int):
    from PIL import ImageFont

    candidates = [
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/yugothb.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    # フォールバック: デフォルトフォント
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _hex(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def make_image() -> bytes:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("❌ Pillow が見つかりません: pip install pillow")
        sys.exit(1)

    PAD = 14   # カード間の余白
    RAD = 28   # 角丸半径

    # (イラストパス, 上部色, 下部色, テキスト色)
    CELL_STYLES = [
        ("picture/icon_kyoyo.png",      _hex("1d4ed8"), _hex("1e40af"), (255,255,255)),
        ("picture/icon_comingsoon.png",  _hex("475569"), _hex("334155"), (148,163,184)),
        ("picture/icon_review.png",      _hex("16a34a"), _hex("15803d"), (255,255,255)),
        ("picture/icon_help.png",        _hex("334155"), _hex("1e293b"), (100,116,139)),
        ("picture/icon_popular.png",     _hex("dc2626"), _hex("b91c1c"), (255,255,255)),
        ("picture/icon_ranking.png",     _hex("d97706"), _hex("b45309"), (255,255,255)),
        None,   # 6: image
        None,   # 7: image
    ]

    font_label = load_font(70)
    font_sm    = load_font(54)   # 長いラベル用

    # ── 背景グラデーション ──
    bg_top = (6, 10, 26)
    bg_bot = (18, 24, 52)
    img  = Image.new("RGB", (W, H), bg_top)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W-1, y)],
                  fill=tuple(int(a + (b-a)*t) for a, b in zip(bg_top, bg_bot)))

    for i, btn in enumerate(BUTTONS):
        col, row = i % COLS, i // COLS
        cx0 = col * CW + PAD
        cy0 = row * RH + PAD
        cx1 = (col + 1) * CW - PAD
        cy1 = (row + 1) * RH - PAD
        cw  = cx1 - cx0
        ch  = cy1 - cy0

        if i in CELL_IMAGES:
            # 画像セルは角丸マスクで貼り込む
            cell = Image.open(CELL_IMAGES[i]).convert("RGB").resize((cw, ch), Image.LANCZOS)
            mask = Image.new("L", (cw, ch), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, cw-1, ch-1], radius=RAD, fill=255)
            img.paste(cell, (cx0, cy0), mask)
            continue

        style = CELL_STYLES[i]
        if not style:
            continue
        icon_path, ctop, cbot, fg = style
        label = btn["label"]

        # ── グラデーションカード ──
        card  = Image.new("RGB", (cw, ch))
        cdraw = ImageDraw.Draw(card)
        for y in range(ch):
            t = y / ch
            cdraw.line([(0, y), (cw-1, y)],
                       fill=tuple(int(a + (b-a)*t) for a, b in zip(ctop, cbot)))
        mask = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, cw-1, ch-1], radius=RAD, fill=255)
        img.paste(card, (cx0, cy0), mask)

        # ── イラスト（上部 55% の領域に収める） ──
        icon_area_h = int(ch * 0.58)
        icon_size   = int(min(cw, icon_area_h) * 0.72)
        icon_img = Image.open(icon_path).convert("RGBA").resize(
            (icon_size, icon_size), Image.LANCZOS
        )
        ix = cx0 + (cw - icon_size) // 2
        iy = cy0 + (icon_area_h - icon_size) // 2
        img.paste(icon_img, (ix, iy), icon_img)

        # ── ラベル（下部） ──
        lf = font_sm if len(label) > 6 else font_label
        bb = draw.textbbox((0, 0), label, font=lf)
        lw = bb[2] - bb[0]
        lx = cx0 + (cw - lw) // 2 - bb[0]
        ly = cy0 + int(ch * 0.68) - bb[1]
        draw.text((lx, ly), label, fill=fg, font=lf)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def load_custom_image(path: str) -> bytes:
    """カスタム画像を読み込む。JPEG以外はPillowでJPEGに変換する。"""
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        if img.size != (W, H):
            print(f"  画像サイズ {img.size} → {W}x{H} にリサイズします")
            img = img.resize((W, H), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()
    except ImportError:
        # Pillowなしの場合はそのまま読む (JPEGのみ)
        with open(path, "rb") as f:
            return f.read()


def main():
    custom_image_path = _args.image

    config = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

    with ApiClient(config) as client:
        api = MessagingApi(client)

        # ── 対象ボットを表示・確認 ──
        bot_info = api.get_bot_info()
        print(f"{'='*50}")
        print(f"対象ボット : {bot_info.display_name}")
        print(f"ボットID   : {bot_info.user_id}")
        print(f"環境       : {_args.env.upper()}")
        print(f"フォームURL: {REVIEW_FORM_URL}")
        print(f"{'='*50}")
        if _args.env == "prod":
            ans = input("⚠️  本番環境に適用します。本当によろしいですか？ (yes と入力): ")
            if ans.strip().lower() != "yes":
                print("中止しました")
                sys.exit(0)

        # 既存のデフォルトリッチメニューを削除
        try:
            existing_id = api.get_default_rich_menu_id().rich_menu_id
            api.cancel_default_rich_menu()
            api.delete_rich_menu(existing_id)
            print(f"既存のリッチメニューを削除: {existing_id}")
        except Exception:
            print("既存のデフォルトリッチメニューなし")

        # リッチメニュー作成
        areas = [
            RichMenuArea(
                bounds=RichMenuBounds(
                    x=(i % COLS) * CW,
                    y=(i // COLS) * RH,
                    width=CW,
                    height=RH,
                ),
                action=btn["action"],
            )
            for i, btn in enumerate(BUTTONS)
        ]

        result = api.create_rich_menu(
            RichMenuRequest(
                size=RichMenuSize(width=W, height=H),
                selected=True,
                name="神大ライフハック",
                chat_bar_text="メニュー",
                areas=areas,
            )
        )
        rich_menu_id = result.rich_menu_id
        print(f"リッチメニュー作成: {rich_menu_id}")

        # 画像を読み込む or 生成する
        if custom_image_path:
            print(f"カスタム画像を使用: {custom_image_path}")
            image_data = load_custom_image(custom_image_path)
        else:
            print("画像を自動生成中...")
            image_data = make_image()

        req = urllib.request.Request(
            f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
            data=image_data,
            headers={
                "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "image/jpeg",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            if resp.status != 200:
                raise RuntimeError(f"画像アップロード失敗: {resp.status}")
        print("画像アップロード完了")

        # デフォルトに設定
        api.set_default_rich_menu(rich_menu_id)
        print(f"[完了] デフォルトリッチメニューに設定しました: {rich_menu_id}")
        print("ボタン配置 (左上から横順):")
        for i, btn in enumerate(BUTTONS):
            print(f"  [{i+1}] {btn['label']}")


if __name__ == "__main__":
    main()
