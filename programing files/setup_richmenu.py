"""
リッチメニューをセットアップするスクリプト。
実行: python setup_richmenu.py --env dev
      python setup_richmenu.py --env prod  (確認プロンプトあり)

必要な環境変数 (.env.dev / .env):
  LINE_CHANNEL_ACCESS_TOKEN
  REVIEW_FORM_URL
  TIMETABLE_LIFF_ID  (My時間割ボタンのLIFF URL用)
"""
import argparse
import io
import os
import sys
import urllib.request

# ── 引数パース ──────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--env", choices=["dev", "prod"], required=True,
                    help="dev=.env.dev, prod=.env")
parser.add_argument("image", nargs="?", default=None,
                    help="カスタム画像パス (省略時: ../picture/6.24リッチメニュー.png)")
args = parser.parse_args()

# ── 環境変数読み込み ─────────────────────────────────────────────────────────
from dotenv import load_dotenv
env_file = ".env.dev" if args.env == "dev" else ".env"
load_dotenv(env_file, override=True)

CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
REVIEW_FORM_URL = os.environ.get(
    "REVIEW_FORM_URL",
    "https://shindairaifuhaku-1.onrender.com" if args.env == "dev"
    else "https://shindairaifuhaku.onrender.com",
)
TIMETABLE_LIFF_ID = os.environ.get("TIMETABLE_LIFF_ID", "")

if args.env == "prod":
    confirm = input("⚠️  本番環境のリッチメニューを更新します。よろしいですか？ (yes/no): ")
    if confirm.strip().lower() != "yes":
        print("キャンセルしました")
        sys.exit(0)

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    RichMenuArea,
    RichMenuBounds,
    RichMenuRequest,
    RichMenuSize,
    MessageAction,
    PostbackAction,
    URIAction,
)

# ── 画像サイズ（元画像の比率をそのまま使用）──────────────────────────────────
W, H = 1959, 803

# ── レイアウト座標（元画像 1959×803 基準）────────────────────────────────────
SIDE_X  = 1680   # 右サイドバー左端
ROW2_Y  = 268    # Row2 開始（1/3H）
ROW3_Y  = 535    # Row3 開始（2/3H）
REV_W   = 430    # レビュー投稿 右端
COL2_X  = SIDE_X // 3        # ≈ 560
COL3_X  = SIDE_X * 2 // 3    # ≈ 1120
COL3B_X = SIDE_X // 2        # ≈ 840
SH      = H // 4              # ≈ 200


def _timetable_action():
    return PostbackAction(label="My時間割", data="時間割", display_text="📅 My時間割")


AREAS = [
    # ── Row 1 ────────────────────────────────────────────────────
    {
        "label": "レビュー投稿",
        "x": 0, "y": 0, "w": REV_W, "h": ROW2_Y,
        "action": URIAction(label="レビュー投稿", uri=REVIEW_FORM_URL),
    },
    {
        "label": "BEEF+バナー",
        "x": REV_W, "y": 0, "w": SIDE_X - REV_W, "h": ROW2_Y,
        "action": URIAction(label="BEEF+", uri="https://beefplus.center.kobe-u.ac.jp/login"),
    },
    # ── Row 2 ────────────────────────────────────────────────────
    {
        "label": "My時間割",
        "x": 0, "y": ROW2_Y, "w": COL2_X, "h": ROW3_Y - ROW2_Y,
        "action": _timetable_action(),
    },
    {
        "label": "教養",
        "x": COL2_X, "y": ROW2_Y, "w": COL3_X - COL2_X, "h": ROW3_Y - ROW2_Y,
        "action": MessageAction(label="教養科目一覧", text="教養"),
    },
    {
        "label": "専門",
        "x": COL3_X, "y": ROW2_Y, "w": SIDE_X - COL3_X, "h": ROW3_Y - ROW2_Y,
        "action": MessageAction(label="専門科目一覧", text="専門"),
    },
    # ── Row 3 ────────────────────────────────────────────────────
    {
        "label": "食堂",
        "x": 0, "y": ROW3_Y, "w": COL3B_X, "h": H - ROW3_Y,
        "action": URIAction(label="食堂メニュー", uri="https://west2-univ.jp/sp/kobe-univ.php"),
    },
    {
        "label": "バイト",
        "x": COL3B_X, "y": ROW3_Y, "w": SIDE_X - COL3B_X, "h": H - ROW3_Y,
        "action": MessageAction(label="バイト", text="バイト"),
    },
    # ── 右サイドバー (4 段) ───────────────────────────────────────
    {
        "label": "図書館",
        "x": SIDE_X, "y": 0, "w": W - SIDE_X, "h": SH,
        "action": URIAction(label="図書館", uri="https://lib.kobe-u.ac.jp/"),
    },
    {
        "label": "市バス",
        "x": SIDE_X, "y": SH, "w": W - SIDE_X, "h": SH,
        "action": URIAction(label="市バス", uri="https://kotsu.city.kobe.lg.jp/"),
    },
    {
        "label": "うりぼーポータル",
        "x": SIDE_X, "y": SH * 2, "w": W - SIDE_X, "h": SH,
        "action": URIAction(
            label="うりぼーポータル",
            uri="https://www.uriboportal.ofc.kobe-u.ac.jp/",
        ),
    },
    {
        "label": "ヘルプ",
        "x": SIDE_X, "y": SH * 3, "w": W - SIDE_X, "h": H - SH * 3,
        "action": MessageAction(label="ヘルプ", text="ヘルプ"),
    },
]


def load_custom_image(path: str) -> bytes:
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        if img.size != (W, H):
            print(f"  ⚠ 画像サイズ {img.size} が設定値 {W}x{H} と異なります（リサイズしません）")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()
    except ImportError:
        with open(path, "rb") as f:
            return f.read()


def main():
    image_path = args.image or "../picture/6.24リッチメニュー.png"

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
        rich_menu_areas = [
            RichMenuArea(
                bounds=RichMenuBounds(
                    x=a["x"], y=a["y"],
                    width=a["w"], height=a["h"],
                ),
                action=a["action"],
            )
            for a in AREAS
        ]

        result = api.create_rich_menu(
            RichMenuRequest(
                size=RichMenuSize(width=W, height=H),
                selected=True,
                name="神大ライフハック",
                chat_bar_text="メニュー",
                areas=rich_menu_areas,
            )
        )
        rich_menu_id = result.rich_menu_id
        print(f"リッチメニュー作成: {rich_menu_id}")

        # 画像アップロード
        print(f"画像を読み込み中: {image_path}")
        image_data = load_custom_image(image_path)

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
        print(f"\n[完了] デフォルトリッチメニューに設定しました: {rich_menu_id}")
        print(f"環境: {args.env}  /  REVIEW_FORM_URL: {REVIEW_FORM_URL}")
        print(f"TIMETABLE_LIFF_ID: {TIMETABLE_LIFF_ID or '(未設定 → メッセージアクション)'}")
        print("\nボタン配置:")
        for a in AREAS:
            print(f"  {a['label']:16s} → {a['action'].__class__.__name__}")


if __name__ == "__main__":
    main()
