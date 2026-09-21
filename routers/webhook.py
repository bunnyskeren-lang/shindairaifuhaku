import asyncio

from fastapi import APIRouter, HTTPException, Request

from core import line_client
from core.activity_log import save_error_log
from core.background_tasks import fire_and_forget
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
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc

    task = asyncio.create_task(process_events(events))

    def _on_process_done(t: asyncio.Task):
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            fire_and_forget(save_error_log(exc, action="process_events_bg"))

    task.add_done_callback(_on_process_done)
    return {"status": "ok"}
