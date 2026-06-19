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

W, H = 2500, 1686   # フル高さメニュー（picture/ricchimenu.png の比率に合わせた）

# ── picture/ricchimenu.png のレイアウト ──────────────────────────
#
#  上段 (h=920):
#    [  レビューを投稿 (w=1000)  ][  BEEFplus (w=1500)          ]
#
#  下段 (h=766):
#    [教養][専門CS][カミCS][うりP][うりN][ヘルプ]  ← 各 ~417px
#
CELLS = [
    # ── 上段 ─────────────────────────────────────────────────────
    dict(x=0,    y=0,   w=1000, h=920,
         label="レビューを投稿",
         action=MessageAction(label="レビュー投稿", text="レビュー投稿")),

    dict(x=1000, y=0,   w=1500, h=920,
         label="BEEFplus",
         action=URIAction(label="BEEFplus", uri=f"{REVIEW_FORM_URL}/r/beefplus")),

    # ── 下段 (6等分) ─────────────────────────────────────────────
    dict(x=0,    y=920, w=416,  h=766,
         label="教養",
         action=MessageAction(label="教養科目一覧", text="教養")),

    dict(x=416,  y=920, w=417,  h=766,
         label="専門 Coming Soon",
         action=MessageAction(label="専門科目一覧", text="専門comingsoon")),

    dict(x=833,  y=920, w=417,  h=766,
         label="カミングスーン",
         action=MessageAction(label="カミングスーン", text="専門comingsoon")),

    dict(x=1250, y=920, w=417,  h=766,
         label="うりぼーポータル",
         action=URIAction(label="うりぼーポータル", uri=f"{REVIEW_FORM_URL}/r/uribop")),

    dict(x=1667, y=920, w=417,  h=766,
         label="うりぼーネット",
         # TODO: 正しいURLに更新
         action=URIAction(label="うりぼーネット", uri=f"{REVIEW_FORM_URL}/r/uribon")),

    dict(x=2084, y=920, w=416,  h=766,
         label="ヘルプ",
         action=MessageAction(label="ヘルプ", text="ヘルプ")),
]


def load_font(size: int):
    from PIL import ImageFont
    candidates = [
        "C:/Windows/Fonts/HGRMB.TTC",
        "C:/Windows/Fonts/HGRME.TTC",
        "C:/Windows/Fonts/BIZ-UDGothicB.ttc",
        "C:/Windows/Fonts/yugothb.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _draw_text_outlined(draw, pos, text, font, fill, outline=(0, 0, 0)):
    """8方向アウトライン付きでテキストを描画。"""
    x, y = pos
    for ox, oy in [(-3,0),(3,0),(0,-3),(0,3),(-2,-2),(2,-2),(-2,2),(2,2)]:
        draw.text((x+ox, y+oy), text, fill=outline, font=font)
    draw.text((x, y), text, fill=fill, font=font)


def make_image() -> bytes:
    """picture/ricchimenu.png を LINE サイズ (2500×1686) にリサイズして返す。"""
    try:
        from PIL import Image
    except ImportError:
        print("❌ Pillow が見つかりません: pip install pillow")
        sys.exit(1)

    img = Image.open("picture/ricchimenu.png").convert("RGB")
    if img.size != (W, H):
        img = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def load_custom_image(path: str) -> bytes:
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
        with open(path, "rb") as f:
            return f.read()


def main():
    custom_image_path = _args.image

    config = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

    with ApiClient(config) as client:
        api = MessagingApi(client)

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

        try:
            existing_id = api.get_default_rich_menu_id().rich_menu_id
            api.cancel_default_rich_menu()
            api.delete_rich_menu(existing_id)
            print(f"既存のリッチメニューを削除: {existing_id}")
        except Exception:
            print("既存のデフォルトリッチメニューなし")

        areas = [
            RichMenuArea(
                bounds=RichMenuBounds(
                    x=cell["x"],
                    y=cell["y"],
                    width=cell["w"],
                    height=cell["h"],
                ),
                action=cell["action"],
            )
            for cell in CELLS
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

        api.set_default_rich_menu(rich_menu_id)
        print(f"[完了] デフォルトリッチメニューに設定しました: {rich_menu_id}")
        print("ボタン配置:")
        for cell in CELLS:
            print(f"  [{cell['x']},{cell['y']} {cell['w']}x{cell['h']}] {cell['label']}")


if __name__ == "__main__":
    main()
