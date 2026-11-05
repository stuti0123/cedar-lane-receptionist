"""Phase 2: the doorway Vapi talks to.

Vapi handles the phone/microphone, speech-to-text and text-to-speech. For every thing the caller says it
POSTs the conversation to  <your public url>/vapi/chat/completions  (the "custom LLM" format that looks like
OpenAI's). We hand the caller's latest sentence to the SAME agent that powers the text chat, and stream the
answer back in small pieces so Vapi can start speaking right away (that is what keeps replies fast).
"""
import asyncio
import json
import logging
import threading
import time
import uuid
from typing import Callable

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import config
from .agent import Agent

log = logging.getLogger(__name__)

GREETING = "Thanks for calling Cedar Lane Auto Detailing. How can I help?"

# If the model/calendar has produced nothing speakable after this long, say a short true filler so Vapi gets
# a first content token quickly (the empty "assistant" role chunk does not count as speech) and the caller
# doesn't hear dead air. Afterwards, send an SSE comment every KEEPALIVE_SECONDS to keep the stream open.
FILLER_AFTER_SECONDS = 4.0
KEEPALIVE_SECONDS = 5.0
SLOW_FILLER = "Sorry, one moment while I look into that. "
_END = object()


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
        session.ctx.confirm_booking = True  # voice: read details back before booking (speech-to-text mishears digits)
        agent = get_agent()
        completion_id = "chatcmpl-" + uuid.uuid4().hex[:12]
        log.info("vapi call %s said: %r", sid, text)

        if body.get("stream", True):
            gone = threading.Event()  # set when Vapi hangs up on this request (it does so when the caller keeps talking)
            pieces = agent.stream(session, text, interim=True, cancelled=gone.is_set) if text else iter([GREETING])

            async def events():
                started = time.monotonic()
                loop = asyncio.get_running_loop()
                box: asyncio.Queue = asyncio.Queue()

                def put(item):
                    try:
                        loop.call_soon_threadsafe(box.put_nowait, item)
                    except RuntimeError:  # the server is shutting down
                        pass

                def work():  # the agent is blocking code (the model + Google): run it beside the stream
                    try:
                        for piece in pieces:
                            put(piece)
                    except Exception:
                        log.exception("vapi call %s: agent crashed", sid)
                        put("Sorry, I'm having trouble right now. Could you say that again in a moment?")
                    finally:
                        put(_END)

                threading.Thread(target=work, daemon=True).start()
                spoke, finished, filler_sent = False, False, False
                try:
                    yield chunk(model, completion_id, {"role": "assistant"})
                    while True:
                        try:
                            piece = await asyncio.wait_for(
                                box.get(), KEEPALIVE_SECONDS if spoke else FILLER_AFTER_SECONDS)
                        except asyncio.TimeoutError:
                            if spoke:
                                yield ": keep-alive\n\n"  # SSE comment: ignored by clients, keeps the connection busy
                            else:
                                spoke = filler_sent = True
                                log.info("vapi call %s: slow answer, sent filler at %.1fs", sid, time.monotonic() - started)
                                yield chunk(model, completion_id, {"content": SLOW_FILLER})
                            continue
                        if piece is _END:
                            break
                        if spoke and piece in Agent.FILLERS and filler_sent:
                            continue  # we already said "one moment" because the model was slow; don't say it twice
                        if piece:
                            if not spoke:
                                log.info("vapi call %s: first words after %.1fs", sid, time.monotonic() - started)
                            spoke = True
                            yield chunk(model, completion_id, {"content": piece})
                    yield chunk(model, completion_id, {}, finish="stop")
                    yield "data: [DONE]\n\n"
                    finished = True
                    log.info("vapi call %s: answer complete in %.1fs", sid, time.monotonic() - started)
                finally:
                    if not finished:  # Vapi hung up / timed out: tell the worker to stop doing anything
                        gone.set()
                        log.warning("vapi call %s: client left after %.1fs, before the answer finished",
                                    sid, time.monotonic() - started)

            return StreamingResponse(events(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        pieces = agent.stream(session, text, interim=True) if text else iter([GREETING])
        answer = "".join(pieces).strip()
        return JSONResponse({
            "id": completion_id, "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
        })

    return router
