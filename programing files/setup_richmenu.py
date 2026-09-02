"""
リッチメニューをセットアップするスクリプト。
実行: python setup_richmenu.py --env dev
      python setup_richmenu.py --env prod  (確認プロンプトあり)

必要な環境変数 (.env.dev / .env):
  LINE_CHANNEL_ACCESS_TOKEN
  REVIEW_FORM_URL
  REVIEW_LIFF_ID     (レビュー投稿ボタンのLIFF URL用)
  CONTACT_LIFF_ID    (お問い合わせボタンのLIFF URL用)
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
CONTACT_LIFF_ID = os.environ.get("CONTACT_LIFF_ID", "")

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

# ── 画像サイズ（2026-08-24 新デザイン画像v2の実寸、空・校舎イラスト背景版）───
W, H = 1624, 969

# ── レイアウト座標（assets/richmenu.png ピクセル実測値、境界は隣接領域の中間点）──
# 画像上部（y:0〜TOP_Y）はタイトルバナー「神大ライフハック」でボタンなし
# → クリック領域未定義（タップしても何も起きない）
TOP_Y   = 210    # タイトルバナー下端 / Row1 上端
ROW2_Y  = 534    # Row1(投稿/閲覧) / Row2(楽単/おみくじ/鬼単) 境界
REV_W   = 676    # レビュー投稿 / レビュー閲覧 境界（Row1）
SIDE_X  = 1215   # 右サイドバー左端
COL_A   = 440    # 楽単5選 / 10連おみくじ 境界（Row2）
COL_B   = 842    # 10連おみくじ / 鬼単5選 境界（Row2）

# サイドバー行区切り（4段）
SY1 = 394   # うりぼーポータル・BEEF+ / 食堂メニュー・生協アプリ 境界
SY2 = 580   # 食堂メニュー・生協アプリ / 市バス・図書館 境界
SY3 = 768   # 市バス・図書館 / 使い方・お問い合わせ 境界
SIDE_MID = 1410  # サイドバー左右2列の境界


def _review_action():
    if REVIEW_LIFF_ID:
        return URIAction(label="レビュー投稿", uri=f"https://liff.line.me/{REVIEW_LIFF_ID}")
    return PostbackAction(label="レビュー投稿", data="レビュー投稿")


AREAS = [
    # ── Row 1（レビュー投稿・レビュー閲覧）─────────────────────────
    {
        "label": "レビュー投稿",
        "x": 0, "y": TOP_Y, "w": REV_W, "h": ROW2_Y - TOP_Y,
        "action": _review_action(),
    },
    {
        # 2026-09-02: 専用の入り口画面（科目名検索を主導線にしたFlexMessage、
        # line_bot/flex_builders.py の make_category_browse_flex()）を実装。
        # 教養/専門タブと系統・学部グリッドを1画面に統合（2026-09-02改修）
        "label": "レビュー閲覧",
        "x": REV_W, "y": TOP_Y, "w": SIDE_X - REV_W, "h": ROW2_Y - TOP_Y,
        "action": PostbackAction(label="レビューを閲覧", data="レビュー閲覧"),
    },
    # ── Row 2（楽単5選・10連おみくじ・鬼単5選）─────────────────────
    {
        "label": "楽単5選",
        "x": 0, "y": ROW2_Y, "w": COL_A, "h": H - ROW2_Y,
        "action": PostbackAction(label="楽単ランキング", data="楽単"),
    },
    {
        "label": "鬼単5選",
        "x": COL_B, "y": ROW2_Y, "w": SIDE_X - COL_B, "h": H - ROW2_Y,
        "action": PostbackAction(label="鬼単ランキング", data="鬼単"),
    },
    {
        "label": "10連おみくじ",
        "x": COL_A, "y": ROW2_Y, "w": COL_B - COL_A, "h": H - ROW2_Y,
        "action": PostbackAction(label="10連おみくじ", data="おみくじ"),
    },
    # ── 右サイドバー（4段 x 2列）────────────────────────────────
    {
        "label": "うりぼーポータル",
        "x": SIDE_X, "y": TOP_Y, "w": SIDE_MID - SIDE_X, "h": SY1 - TOP_Y,
        "action": URIAction(
            label="うりぼーポータル",
            uri="https://www.uriboportal.ofc.kobe-u.ac.jp/?openExternalBrowser=1",
        ),
    },
    {
        "label": "BEEF+",
        "x": SIDE_MID, "y": TOP_Y, "w": W - SIDE_MID, "h": SY1 - TOP_Y,
        "action": URIAction(label="BEEF+", uri="https://beefplus.center.kobe-u.ac.jp/login?openExternalBrowser=1"),
    },
    {
        "label": "食堂メニュー",
        "x": SIDE_X, "y": SY1, "w": SIDE_MID - SIDE_X, "h": SY2 - SY1,
        "action": URIAction(label="食堂メニュー", uri="https://west2-univ.jp/sp/kobe-univ.php"),
    },
    {
        "label": "生協アプリ",
        "x": SIDE_MID, "y": SY1, "w": W - SIDE_MID, "h": SY2 - SY1,
        "action": URIAction(label="生協アプリ", uri=f"{REVIEW_FORM_URL}/coop"),
    },
    {
        "label": "市バス",
        "x": SIDE_X, "y": SY2, "w": SIDE_MID - SIDE_X, "h": SY3 - SY2,
        "action": URIAction(label="市バス", uri="https://kotsu.city.kobe.lg.jp/"),
    },
    {
        "label": "図書館",
        "x": SIDE_MID, "y": SY2, "w": W - SIDE_MID, "h": SY3 - SY2,
        "action": URIAction(label="図書館", uri="https://lib.kobe-u.ac.jp/services/barcode/"),
    },
    {
        "label": "使い方",
        "x": SIDE_X, "y": SY3, "w": SIDE_MID - SIDE_X, "h": H - SY3,
        "action": PostbackAction(label="使い方", data="使い方"),
    },
    {
        "label": "お問い合わせ",
        "x": SIDE_MID, "y": SY3, "w": W - SIDE_MID, "h": H - SY3,
        "action": URIAction(
            label="お問い合わせ",
            uri=f"https://liff.line.me/{CONTACT_LIFF_ID}" if CONTACT_LIFF_ID else f"{REVIEW_FORM_URL}/contact",
        ),
    },
]


# 登録前ユーザー用: 全ボタンを会員登録LIFFへのURIActionにした同一画像のリッチメニュー。
_register_button_uri = f"https://liff.line.me/{REGISTER_LIFF_ID}"

PREREG_AREAS = [
    {**a, "action": URIAction(label="会員登録", uri=_register_button_uri)}
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

        # 前回実行分の残骸（登録前用・通常メニュー両方）を削除
        # （デフォルトはどちらか一方だけなので、上のロジックでは非デフォルト側が消えない）
        _delete_richmenus_by_name(api, "神大ライフハック（登録前）")
        _delete_richmenus_by_name(api, "神大ライフハック")

        print(f"画像を読み込み中: {image_path}")
        image_data = load_custom_image(image_path)

        # 通常メニュー（デフォルトにはせず、登録完了ユーザーへ個別リンクする）
        main_id = _create_and_upload(api, "神大ライフハック", AREAS, image_data)
        print(f"[完了] 通常リッチメニューを作成しました: {main_id}")

        # 登録前メニュー（全ボタン→会員登録LIFF）。LINE側のデフォルトに設定する。
        # 未登録ユーザーへのlink_rich_menu個別呼び出し(line_bot/handler.py)が万一失敗しても、
        # デフォルトが「制限なしの通常メニュー」だと全機能が使えてしまう抜け道になっていたため
        # (2026-08-31発覚)、デフォルトを安全側（登録前メニュー）に倒す設計に変更した。
        prereg_id = ""
        if PREREG_AREAS:
            prereg_id = _create_and_upload(api, "神大ライフハック（登録前）", PREREG_AREAS, image_data)
            api.set_default_rich_menu(prereg_id)
            print(f"[完了] 登録前リッチメニューをデフォルトに設定しました: {prereg_id}")
        else:
            print("[警告] REGISTER_LIFF_ID が未設定のため、登録前リッチメニューは作成されませんでした")
            print("[警告] 登録前メニューが無いため、代わりに通常メニューをデフォルトに設定します")
            api.set_default_rich_menu(main_id)

        print(f"\n環境: {args.env}  /  REVIEW_FORM_URL: {REVIEW_FORM_URL}")
        print("\nボタン配置（通常メニュー）:")
        for a in AREAS:
            print(f"  {a['label']:16s} → {a['action'].__class__.__name__}")

        print("\n次の環境変数を設定してください:")
        print(f"  RICHMENU_ID_MAIN={main_id}")
        if prereg_id:
            print(f"  RICHMENU_ID_PREREGISTER={prereg_id}")
        env_file = ".env.dev" if args.env == "dev" else ".env"
        print(f"  1. programing files/{env_file} に上記を追記")
        svc = "shindairaifuhaku-1（dev）" if args.env == "dev" else "shindairaifuhaku（本番）"
        print(f"  2. Render の {svc} サービスの Environment にも同じ値を追加してください")


if __name__ == "__main__":
    main()
