"""Phase 2 doorway: the endpoint Vapi calls. Offline: a scripted fake model stands in for Gemini."""
import json

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
from fastapi import FastAPI  # noqa: E402

from app import config  # noqa: E402
from app.agent import Agent  # noqa: E402
from app.llm import ToolCall, Turn  # noqa: E402
from app.sessions import SessionStore  # noqa: E402
from app.tools import Deps  # noqa: E402
from app.vapi import call_id_of, create_router, last_user_text  # noqa: E402
from fakes import FakeLedger  # noqa: E402


class ScriptedLLM:
    """Plays back prepared turns; remembers how long the history was each time it was asked."""

    def __init__(self, *turns):
        self.turns, self.history_sizes = list(turns), []

    def user_message(self, text):
        return {"role": "user", "text": text}

    def complete(self, history, system):
        self.history_sizes.append(len(history))
        return self.turns.pop(0)

    def tool_results_message(self, results):
        return {"role": "tool", "results": [out for _, out in results]}


def say(text):
    return Turn(text, [], {"role": "model", "text": text})


def check_thursday():
    call = ToolCall("c1", "check_availability", dict(date="2026-11-05", service="interior_detail_wash",
                                                      preferred_time="15:30"))
    return Turn("", [call], {"role": "model", "call": "check"})


def make_client(booking, llm):
    agent = Agent(Deps(booking, FakeLedger()), llm=llm)
    store = SessionStore(agent.new_session)
    app = FastAPI()
    app.include_router(create_router(lambda: agent, store))
    return fastapi_testclient.TestClient(app), store


def body(text, call_id="call-1", stream=True):
    return {"model": "cedar-lane", "stream": stream, "call": {"id": call_id},
            "messages": [{"role": "system", "content": "ignored"}, {"role": "user", "content": text}]}


def sse(response):
    """Parse the event stream into a list of JSON chunks (plus the literal [DONE])."""
    lines = [l[len("data: "):] for l in response.text.split("\n\n") if l.startswith("data: ")]
    return [l if l == "[DONE]" else json.loads(l) for l in lines]


def test_streams_filler_first_then_the_answer_in_openai_format(booking):
    llm = ScriptedLLM(check_thursday(), say("That one's full. 4:30 is open, want it?"))
    client, _ = make_client(booking, llm)
    r = client.post("/vapi/chat/completions", json=body("Can I book 3:30 Thursday?"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = sse(r)
    assert events[-1] == "[DONE]" and events[-2]["choices"][0]["finish_reason"] == "stop"
    assert events[0]["choices"][0]["delta"]["role"] == "assistant"
    spoken = [e["choices"][0]["delta"]["content"] for e in events[:-1] if "content" in e["choices"][0]["delta"]]
    assert spoken == ["Let me check. ", "That one's full. 4:30 is open, want it?"]  # filler arrives BEFORE the lookup result
    assert all(e["object"] == "chat.completion.chunk" for e in events[:-1])


def test_non_streaming_request_gets_plain_json(booking):
    client, _ = make_client(booking, ScriptedLLM(say("Hello there.")))
    r = client.post("/vapi/chat/completions", json=body("hi", stream=False))
    assert r.json()["choices"][0]["message"]["content"] == "Hello there."


def test_same_call_keeps_memory_and_new_call_starts_fresh(booking):
    llm = ScriptedLLM(say("one"), say("two"), say("three"))
    client, _ = make_client(booking, llm)
    for call_id in ("A", "A", "B"):
        client.post("/vapi/chat/completions", json=body("hi", call_id=call_id, stream=False))
    assert llm.history_sizes == [1, 3, 1]  # call A grew (user, model, user); call B began empty


def test_secret_is_enforced_when_configured(booking, monkeypatch):
    monkeypatch.setattr(config, "VAPI_SECRET", "s3cret")
    client, _ = make_client(booking, ScriptedLLM(say("ok")))
    assert client.post("/vapi/chat/completions", json=body("hi", stream=False)).status_code == 401
    bad = client.post("/vapi/chat/completions", json=body("hi", stream=False), headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    good = client.post("/vapi/chat/completions", json=body("hi", stream=False), headers={"Authorization": "Bearer s3cret"})
    assert good.status_code == 200


def test_missing_call_id_single_message_starts_a_new_call(booking):
    llm = ScriptedLLM(say("a"), say("b"))
    client, _ = make_client(booking, llm)
    no_id = {"stream": False, "messages": [{"role": "user", "content": "hi"}]}
    client.post("/vapi/chat/completions", json=no_id)
    client.post("/vapi/chat/completions", json=no_id)  # a second call that also begins with one user message
    assert llm.history_sizes == [1, 1]


def test_empty_input_gets_the_greeting_without_asking_the_model(booking):
    llm = ScriptedLLM()  # would raise if the model were consulted
    client, _ = make_client(booking, llm)
    r = client.post("/vapi/chat/completions", json={"stream": False, "call": {"id": "x"}, "messages": []})
    assert "Cedar Lane" in r.json()["choices"][0]["message"]["content"]


def test_helpers():
    assert last_user_text([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]) == "a"
    assert last_user_text([{"role": "user", "content": [{"type": "text", "text": "hello"}]}]) == "hello"
    assert last_user_text([]) == ""
    assert call_id_of({"call": {"id": "abc"}}) == "abc" and call_id_of({}) is None


def test_text_chat_never_speaks_fillers(booking):
    """reply() (the Phase 1 chat) must return only the final answer."""
    llm = ScriptedLLM(check_thursday(), say("Final answer."))
    agent = Agent(Deps(booking, FakeLedger()), llm=llm)
    assert agent.reply(agent.new_session("s"), "q") == "Final answer."
