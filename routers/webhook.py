import asyncio

from fastapi import APIRouter, HTTPException, Request

from core import line_client
from core.activity_log import save_error_log
from core.security import verify_line_signature
from line_bot.handler import process_events

router = APIRouter()


@router.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()

    if not verify_line_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        events = line_client.parser.parse(body.decode("utf-8"), signature)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    task = asyncio.create_task(process_events(events))

    def _on_process_done(t: asyncio.Task):
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            asyncio.create_task(save_error_log(exc, action="process_events_bg"))

    task.add_done_callback(_on_process_done)
    return {"status": "ok"}
