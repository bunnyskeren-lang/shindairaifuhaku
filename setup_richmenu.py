"""
リッチメニューをセットアップするスクリプト。
実行: python setup_richmenu.py

必要な環境変数:
  LINE_CHANNEL_ACCESS_TOKEN
  REVIEW_FORM_URL (省略可、デフォルト: https://shindairaifuhaku-1.onrender.com)
"""
import io
import os
import sys
import urllib.request

from dotenv import load_dotenv
load_dotenv()

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
REVIEW_FORM_URL = os.environ.get(
    "REVIEW_FORM_URL", "https://shindairaifuhaku-1.onrender.com"
)

# リッチメニューサイズ (4列×2行)
W, H = 2500, 843
COLS, ROWS = 4, 2
CW, RH = W // COLS, H // ROWS  # 625 x 421

BUTTONS = [
    # 上段
    {
        "label": "教養科目一覧",
        "color": "#0ea5e9",
        "action": MessageAction(label="教養科目一覧", text="教養"),
    },
    {
        "label": "専門科目一覧",
        "color": "#1d4ed8",
        "action": MessageAction(label="専門科目一覧", text="専門"),
    },
    {
        "label": "レビュー投稿",
        "color": "#16a34a",
        "action": URIAction(label="レビュー投稿", uri=REVIEW_FORM_URL),
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
        "label": "うりぼーネット",
        "color": "#0f766e",
        "action": URIAction(label="うりぼーネット", uri="https://knosos.center.kobe-u.ac.jp"),
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


def make_image() -> bytes:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("❌ Pillow が見つかりません。インストールしてください: pip install pillow")
        sys.exit(1)

    img = Image.new("RGB", (W, H), "#1e293b")
    draw = ImageDraw.Draw(img)
    font = load_font(100)

    for i, btn in enumerate(BUTTONS):
        col, row = i % COLS, i // COLS
        x0, y0 = col * CW, row * RH
        x1, y1 = x0 + CW - 1, y0 + RH - 1

        # セル背景
        draw.rectangle([x0, y0, x1, y1], fill=btn["color"])
        # 境界線
        draw.rectangle([x0, y0, x1, y1], outline="#ffffff", width=5)

        # テキストを中央に配置
        bbox = draw.textbbox((0, 0), btn["label"], font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            (x0 + (CW - tw) // 2, y0 + (RH - th) // 2),
            btn["label"],
            fill="#ffffff",
            font=font,
        )

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
    # 使い方: python setup_richmenu.py [カスタム画像パス]
    custom_image_path = sys.argv[1] if len(sys.argv) > 1 else None

    config = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

    with ApiClient(config) as client:
        api = MessagingApi(client)

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
