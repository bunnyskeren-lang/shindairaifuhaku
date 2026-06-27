import urllib.request, ssl, os, sys
from dotenv import load_dotenv

# dev トークンで画像取得
load_dotenv(os.path.join(os.path.dirname(__file__), "programing files/.env.dev"))
DEV_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]

# prod トークン
load_dotenv(os.path.join(os.path.dirname(__file__), "programing files/.env"), override=True)
PROD_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]

def get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as r:
        return r.read()

def post(url, token, data, content_type="application/json"):
    import json
    if isinstance(data, dict):
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                  headers={"Authorization": f"Bearer {token}", "Content-Type": content_type})
    with urllib.request.urlopen(req) as r:
        return r.read()

import json

# 1. dev のデフォルトリッチメニューID取得
dev_id_raw = get("https://api.line.me/v2/bot/user/all/richmenu", DEV_TOKEN)
dev_id = json.loads(dev_id_raw)["richMenuId"]
print(f"dev richMenuId: {dev_id}")

# 2. dev のリッチメニュー設定取得
dev_menu = json.loads(get(f"https://api.line.me/v2/bot/richmenu/{dev_id}", DEV_TOKEN))
print(f"dev menu name: {dev_menu['name']}")

# 3. dev のリッチメニュー画像取得
req = urllib.request.Request(
    f"https://api-data.line.me/v2/bot/richmenu/{dev_id}/content",
    headers={"Authorization": f"Bearer {DEV_TOKEN}"}
)
with urllib.request.urlopen(req) as r:
    image_data = r.read()
print(f"画像取得: {len(image_data)} bytes")

# 4. prod の既存リッチメニューを削除
try:
    prod_id_raw = get("https://api.line.me/v2/bot/user/all/richmenu", PROD_TOKEN)
    prod_id = json.loads(prod_id_raw)["richMenuId"]
    post(f"https://api.line.me/v2/bot/richmenu/{prod_id}/unlink", PROD_TOKEN, {})
    req_del = urllib.request.Request(
        f"https://api.line.me/v2/bot/richmenu/{prod_id}",
        method="DELETE",
        headers={"Authorization": f"Bearer {PROD_TOKEN}"}
    )
    urllib.request.urlopen(req_del)
    print(f"prod 既存リッチメニュー削除: {prod_id}")
except Exception as e:
    print(f"prod 既存リッチメニューなし or 削除失敗: {e}")

# 5. prod にリッチメニュー作成（同じ設定で）
new_menu = {
    "size": dev_menu["size"],
    "selected": dev_menu["selected"],
    "name": dev_menu["name"],
    "chatBarText": dev_menu["chatBarText"],
    "areas": dev_menu["areas"],
}
result = json.loads(post("https://api.line.me/v2/bot/richmenu", PROD_TOKEN, new_menu))
new_id = result["richMenuId"]
print(f"prod リッチメニュー作成: {new_id}")

# 6. 画像アップロード
req_img = urllib.request.Request(
    f"https://api-data.line.me/v2/bot/richmenu/{new_id}/content",
    data=image_data, method="POST",
    headers={"Authorization": f"Bearer {PROD_TOKEN}", "Content-Type": "image/jpeg"}
)
urllib.request.urlopen(req_img)
print("画像アップロード完了")

# 7. デフォルトに設定
post(f"https://api.line.me/v2/bot/user/all/richmenu/{new_id}", PROD_TOKEN, {})
print(f"[完了] prod デフォルトリッチメニューに設定: {new_id}")
