"""
リッチメニューをセットアップするスクリプト。
実行: python setup_richmenu.py --env dev
      python setup_richmenu.py --env prod  (確認プロンプトあり)

必要な環境変数 (.env.dev / .env):
  LINE_CHANNEL_ACCESS_TOKEN
  REVIEW_FORM_URL
  REVIEW_LIFF_ID     (レビュー投稿ボタンのLIFF URL用)
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
                    help="カスタム画像パス (省略時: assets/richmenu.png)")
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
REGISTER_LIFF_ID = os.environ.get("REGISTER_LIFF_ID", "")
REVIEW_LIFF_ID = os.environ.get("REVIEW_LIFF_ID", "")

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
    PostbackAction,
    URIAction,
)

# ── 画像サイズ（元画像の比率をそのまま使用）──────────────────────────────────
W, H = 1736, 906

# ── レイアウト座標（元画像 1736×906 基準、リッチメニュー(7.14).png ピクセル実測値）──
SIDE_X  = 1514   # 右サイドバー左端
ROW2_Y  = 427    # Row1/2 境界
ROW3_Y  = 665    # Row2/3 境界
REV_W   = 472    # レビュー投稿 右端
# 2026-07-30 My時間割機能廃止でボタンを削除。画像(assets/richmenu.png)側は
# My時間割/教養/専門の3分割レイアウトのまま未更新のため、x=0〜COL2_Xは
# クリック領域未定義（TODO: 画像を2分割レイアウトに再デザインしたらCOL2_Xも調整すること）
COL2_X  = 610    # 教養 左端（旧: My時間割/教養 境界）
COL3_X  = 1092   # 教養/専門 境界
R3C1_X  = 426    # 生協メニュー/生協アプリ 境界（Row3）
R3C2_X  = 736    # 生協アプリ/近隣飲食店 境界（Row3）
R3C3_X  = 1091   # 近隣飲食店/バイト 境界（Row3）

# サイドバー行区切り
SY1 = 245   # 図書館 / 市バス 境界
SY2 = 480   # 市バス / うりぼーポータル 境界
SY3 = 730   # うりぼーポータル / ヘルプ 境界


def _review_action():
    if REVIEW_LIFF_ID:
        return URIAction(label="レビュー投稿", uri=f"https://liff.line.me/{REVIEW_LIFF_ID}")
    return PostbackAction(label="レビュー投稿", data="レビュー投稿")


AREAS = [
    # ── Row 1 ────────────────────────────────────────────────────
    {
        "label": "レビュー投稿",
        "x": 0, "y": 0, "w": REV_W, "h": ROW2_Y,
        "action": _review_action(),
    },
    {
        "label": "BEEF+バナー",
        "x": REV_W, "y": 0, "w": SIDE_X - REV_W, "h": ROW2_Y,
        "action": URIAction(label="BEEF+", uri="https://beefplus.center.kobe-u.ac.jp/login?openExternalBrowser=1"),
    },
    # ── Row 2 ────────────────────────────────────────────────────
    # x=0〜COL2_X（旧My時間割ボタン領域）はクリック領域未定義（上のTODO参照）
    {
        "label": "教養",
        "x": COL2_X, "y": ROW2_Y, "w": COL3_X - COL2_X, "h": ROW3_Y - ROW2_Y,
        "action": PostbackAction(label="教養科目一覧", data="教養"),
    },
    {
        "label": "専門",
        "x": COL3_X, "y": ROW2_Y, "w": SIDE_X - COL3_X, "h": ROW3_Y - ROW2_Y,
        "action": PostbackAction(label="専門科目一覧", data="専門"),
    },
    # ── Row 3 ────────────────────────────────────────────────────
    {
        "label": "生協メニュー",
        "x": 0, "y": ROW3_Y, "w": R3C1_X, "h": H - ROW3_Y,
        "action": URIAction(label="生協メニュー", uri="https://west2-univ.jp/sp/kobe-univ.php"),
    },
    {
        "label": "生協アプリ",
        "x": R3C1_X, "y": ROW3_Y, "w": R3C2_X - R3C1_X, "h": H - ROW3_Y,
        "action": URIAction(label="生協アプリ", uri=f"{REVIEW_FORM_URL}/coop"),
    },
    {
        "label": "近隣飲食店",
        "x": R3C2_X, "y": ROW3_Y, "w": R3C3_X - R3C2_X, "h": H - ROW3_Y,
        "action": PostbackAction(label="近隣飲食店", data="近隣飲食店"),
    },
    {
        "label": "バイト",
        "x": R3C3_X, "y": ROW3_Y, "w": SIDE_X - R3C3_X, "h": H - ROW3_Y,
        "action": PostbackAction(label="バイト", data="バイト"),
    },
    # ── 右サイドバー (4 段) ───────────────────────────────────────
    {
        "label": "図書館",
        "x": SIDE_X, "y": 0, "w": W - SIDE_X, "h": SY1,
        "action": URIAction(label="図書館", uri="https://lib.kobe-u.ac.jp/"),
    },
    {
        "label": "市バス",
        "x": SIDE_X, "y": SY1, "w": W - SIDE_X, "h": SY2 - SY1,
        "action": URIAction(label="市バス", uri="https://kotsu.city.kobe.lg.jp/"),
    },
    {
        "label": "うりぼーポータル",
        "x": SIDE_X, "y": SY2, "w": W - SIDE_X, "h": SY3 - SY2,
        "action": URIAction(
            label="うりぼーポータル",
            uri="https://www.uriboportal.ofc.kobe-u.ac.jp/?openExternalBrowser=1",
        ),
    },
    {
        "label": "ヘルプ",
        "x": SIDE_X, "y": SY3, "w": W - SIDE_X, "h": H - SY3,
        "action": PostbackAction(label="ヘルプ", data="ヘルプ"),
    },
]


# 登録前ユーザー用: 全ボタンを会員登録LIFFへのURIActionにした同一画像のリッチメニュー
PREREG_AREAS = [
    {**a, "action": URIAction(label="会員登録", uri=f"https://liff.line.me/{REGISTER_LIFF_ID}")}
    for a in AREAS
] if REGISTER_LIFF_ID else []


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


def _delete_richmenus_by_name(api, name: str) -> None:
    for rm in api.get_rich_menu_list().richmenus:
        if rm.name == name:
            api.delete_rich_menu(rm.rich_menu_id)
            print(f"同名リッチメニューを削除: {rm.rich_menu_id} ({name})")


def _create_and_upload(api, name: str, areas: list, image_data: bytes) -> str:
    rich_menu_areas = [
        RichMenuArea(
            bounds=RichMenuBounds(x=a["x"], y=a["y"], width=a["w"], height=a["h"]),
            action=a["action"],
        )
        for a in areas
    ]
    result = api.create_rich_menu(
        RichMenuRequest(
            size=RichMenuSize(width=W, height=H),
            selected=True,
            name=name,
            chat_bar_text="メニュー",
            areas=rich_menu_areas,
        )
    )
    rich_menu_id = result.rich_menu_id
    print(f"リッチメニュー作成: {rich_menu_id} ({name})")

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
    print(f"画像アップロード完了 ({name})")
    return rich_menu_id


def main():
    image_path = args.image or "assets/richmenu.png"

    config = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

    with ApiClient(config) as client:
        api = MessagingApi(client)

        # 既存のデフォルトリッチメニューを削除
        try:
            existing_id = api.get_default_rich_menu_id().rich_menu_id
            api.cancel_default_rich_menu()
            api.delete_rich_menu(existing_id)
            print(f"既存のデフォルトリッチメニューを削除: {existing_id}")
        except Exception:
            print("既存のデフォルトリッチメニューなし")

        # 前回実行分の登録前用リッチメニューが残っていれば削除（デフォルトではないため上のロジックでは消えない）
        _delete_richmenus_by_name(api, "神大ライフハック（登録前）")

        print(f"画像を読み込み中: {image_path}")
        image_data = load_custom_image(image_path)

        # 通常メニュー（デフォルトに設定）
        main_id = _create_and_upload(api, "神大ライフハック", AREAS, image_data)
        api.set_default_rich_menu(main_id)
        print(f"[完了] デフォルトリッチメニューに設定しました: {main_id}")

        # 登録前メニュー（全ボタン→会員登録LIFF。デフォルトにはせず、未登録ユーザーへ個別リンクする）
        prereg_id = ""
        if PREREG_AREAS:
            prereg_id = _create_and_upload(api, "神大ライフハック（登録前）", PREREG_AREAS, image_data)
            print(f"[完了] 登録前リッチメニューを作成しました: {prereg_id}")
        else:
            print("[警告] REGISTER_LIFF_ID が未設定のため、登録前リッチメニューは作成されませんでした")

        print(f"\n環境: {args.env}  /  REVIEW_FORM_URL: {REVIEW_FORM_URL}")
        print("\nボタン配置（通常メニュー）:")
        for a in AREAS:
            print(f"  {a['label']:16s} → {a['action'].__class__.__name__}")

        if prereg_id:
            print(f"\n次の環境変数を設定してください: RICHMENU_ID_PREREGISTER={prereg_id}")
            print("  1. programing files/.env" + (".dev" if args.env == "dev" else "") + f" に RICHMENU_ID_PREREGISTER={prereg_id} を追記")
            svc = "shindairaifuhaku-1（dev）" if args.env == "dev" else "shindairaifuhaku（本番）"
            print(f"  2. Render の {svc} サービスの Environment にも同じ値を追加してください")


if __name__ == "__main__":
    main()
