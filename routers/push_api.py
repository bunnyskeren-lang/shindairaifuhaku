import secrets as py_secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import PUSH_ENABLE_TOKEN, VAPID_PUBLIC_KEY
from database import AsyncSessionLocal
from models import PushSubscription

router = APIRouter()


def _valid_token(token: str) -> bool:
    # 修正理由: 管理画面ログイン(セッションCookie・TTL4時間)に通知購読が依存すると、
    # iPhoneでアプリがOSに回収されCookieが消えるたびに再ログインしないと通知が
    # 届かなくなり不便だった(2026-09-01)。この秘密トークンだけで購読できる専用の
    # 入口を分離し、一度購読すれば管理画面の再ログイン状況に関係なく通知が届き続けるようにする。
    return bool(PUSH_ENABLE_TOKEN) and py_secrets.compare_digest(token, PUSH_ENABLE_TOKEN)


@router.get("/push/enable", response_class=HTMLResponse)
async def push_enable_page(token: str = ""):
    if not _valid_token(token):
        raise HTTPException(status_code=404)
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>通知の設定</title></head>
<body style="font-family:sans-serif;padding:2em;text-align:center;">
<p id="msg">通知を有効化しています…</p>
<script>
(function() {{
  var TOKEN = {token!r};
  var VAPID_KEY = {VAPID_PUBLIC_KEY!r};
  function urlB64(b64) {{
    var pad = '='.repeat((4 - b64.length % 4) % 4);
    var raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    var arr = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
    return arr;
  }}
  function setMsg(t) {{ document.getElementById('msg').textContent = t; }}
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {{
    setMsg('このブラウザ・アプリの開き方では通知に対応していません');
    return;
  }}
  navigator.serviceWorker.register('/sw.js').then(function(reg) {{
    return reg.pushManager.getSubscription().then(function(sub) {{
      if (sub) return sub;
      return reg.pushManager.subscribe({{ userVisibleOnly: true, applicationServerKey: urlB64(VAPID_KEY) }});
    }});
  }}).then(function(sub) {{
    return fetch('/push/subscribe?token=' + encodeURIComponent(TOKEN), {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(sub.toJSON()),
    }});
  }}).then(function(res) {{
    setMsg(res.ok ? '通知を有効にしました。このページは閉じて大丈夫です' : '登録に失敗しました');
  }}).catch(function() {{
    setMsg('通知の許可が必要です。ホーム画面に追加したアイコンから開き直してお試しください');
  }});
}})();
</script>
</body></html>"""
    return HTMLResponse(html)


@router.post("/push/subscribe")
async def push_subscribe(request: Request, token: str = ""):
    if not _valid_token(token):
        raise HTTPException(status_code=404)
    data = await request.json()
    try:
        endpoint = data["endpoint"]
        p256dh = data["keys"]["p256dh"]
        auth = data["keys"]["auth"]
    except (KeyError, TypeError):
        raise HTTPException(status_code=400, detail="invalid subscription payload")
    async with AsyncSessionLocal() as session:
        stmt = pg_insert(PushSubscription).values(
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
        ).on_conflict_do_update(
            index_elements=["endpoint"],
            set_={"p256dh": p256dh, "auth": auth},
        )
        await session.execute(stmt)
        await session.commit()
    return {"ok": True}
