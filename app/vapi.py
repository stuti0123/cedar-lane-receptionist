"""Phase 2: the doorway Vapi talks to.

Vapi handles the phone/microphone, speech-to-text and text-to-speech. For every thing the caller says it
POSTs the conversation to  <your public url>/vapi/chat/completions  (the "custom LLM" format that looks like
OpenAI's). We hand the caller's latest sentence to the SAME agent that powers the text chat, and stream the
answer back in small pieces so Vapi can start speaking right away (that is what keeps replies fast).
"""
import json
import logging
import time
import uuid
from typing import Callable

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import config

log = logging.getLogger(__name__)

GREETING = "Thanks for calling Cedar Lane Auto Detailing. How can I help?"


def last_user_text(messages: list) -> str:
    """The caller's newest sentence. Vapi resends the whole conversation each time; we only need the last turn,
    because the agent keeps its own memory of the call."""
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, list):  # some clients send [{"type": "text", "text": "..."}]
            content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
        return (content or "").strip()
    return ""


def call_id_of(body: dict) -> str | None:
    return (body.get("call") or {}).get("id") or (body.get("metadata") or {}).get("callId")


def chunk(model: str, completion_id: str, delta: dict, finish: str | None = None) -> str:
    """One Server-Sent-Event line in the OpenAI 'chat.completion.chunk' format."""
    payload = {"id": completion_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
               "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(payload)}\n\n"


def create_router(get_agent: Callable, store) -> APIRouter:
    router = APIRouter(prefix="/vapi")

    def check_secret(authorization: str | None) -> None:
        if config.VAPI_SECRET and authorization != f"Bearer {config.VAPI_SECRET}":
            raise HTTPException(status_code=401, detail="bad or missing secret")

    @router.post("/chat/completions")
    async def completions(request: Request, authorization: str | None = Header(default=None)):
        check_secret(authorization)
        body = await request.json()
        messages = body.get("messages") or []
        model = body.get("model") or "cedar-lane"
        text = last_user_text(messages)

        # which call is this? Vapi sends the call id; if it ever doesn't, treat a request with a single
        # user message as the start of a brand-new call.
        sid = call_id_of(body)
        if sid is None:
            sid = "vapi-unknown-call"
            if sum(1 for m in messages if m.get("role") == "user") <= 1:
                store.drop(sid)
        session = store.get(sid)
        agent = get_agent()
        completion_id = "chatcmpl-" + uuid.uuid4().hex[:12]
        log.info("vapi call %s said: %r", sid, text)

        pieces = agent.stream(session, text, interim=True) if text else iter([GREETING])

        if body.get("stream", True):
            def events():
                yield chunk(model, completion_id, {"role": "assistant"})
                for piece in pieces:
                    if piece:
                        yield chunk(model, completion_id, {"content": piece})
                yield chunk(model, completion_id, {}, finish="stop")
                yield "data: [DONE]\n\n"

            return StreamingResponse(events(), media_type="text/event-stream")

        answer = "".join(pieces).strip()
        return JSONResponse({
            "id": completion_id, "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
        })

    return router
