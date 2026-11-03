"""Tiny web server: POST /chat for the agent, GET / for a bare chat page. Phase 2 (Vapi) will call
the same Agent from another endpoint."""
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import Agent
from .sessions import SessionStore
from .vapi import create_router
from .wiring import build_agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@lru_cache
def get_agent() -> Agent:
    return build_agent()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_agent()  # build now: a bad config or Google permission problem stops the server at startup
    log.info("Receptionist ready")
    yield


app = FastAPI(title="Cedar Lane receptionist", lifespan=lifespan)
store: SessionStore = SessionStore(lambda sid: get_agent().new_session(sid))


app.include_router(create_router(get_agent, store))  # Phase 2: Vapi voice


class ChatIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)


@app.post("/chat")
def chat(body: ChatIn) -> dict:
    try:
        return {"reply": get_agent().reply(store.get(body.session_id), body.message)}
    except Exception:
        log.exception("chat failed (session %s)", body.session_id)
        raise HTTPException(status_code=500, detail="Something went wrong. Check the server log.")


@app.post("/reset/{session_id}")
def reset(session_id: str) -> dict:
    store.drop(session_id)
    return {"ok": True}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "open_conversations": len(store)}


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent.parent / "static" / "index.html")
