"""Tiny web server: POST /chat for the agent, GET / for a bare chat page. Phase 2 (Vapi) will call
the same Agent from another endpoint."""
import logging
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .agent import Agent, Session
from .booking import Booking
from .gcal import GoogleCalendar, google_service
from .sheets import SheetsLedger
from .tools import Deps

logging.basicConfig(level=logging.INFO)
app = FastAPI()
sessions: dict[str, Session] = {}


@lru_cache
def get_agent() -> Agent:
    ledger = SheetsLedger(google_service("sheets", "v4"))
    ledger.ensure_tabs()
    return Agent(Deps(booking=Booking(GoogleCalendar()), ledger=ledger))


class ChatIn(BaseModel):
    session_id: str
    message: str


@app.post("/chat")
def chat(body: ChatIn) -> dict:
    agent = get_agent()
    session = sessions.setdefault(body.session_id, agent.new_session(body.session_id))
    return {"reply": agent.reply(session, body.message)}


@app.post("/reset/{session_id}")
def reset(session_id: str) -> dict:
    sessions.pop(session_id, None)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent.parent / "static" / "index.html")
