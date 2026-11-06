"""The conversation loop, with scripted fake AI clients (no network, no API key). It checks the plumbing:
tool calls are executed, results go back to the model, and the final text is returned. Both providers."""
import json
from types import SimpleNamespace as NS

from google.genai import types
import pytest

from app.agent import Agent, system_prompt
from app.llm import AnthropicLLM, GeminiLLM
from app.tools import Deps
from conftest import NOW
from fakes import FakeLedger

CHECK_ARGS = dict(date="2026-11-05", service="interior_detail_wash", preferred_time="15:30")


# ---------------- Anthropic ----------------
def a_tool_use(id_, name, **inp):
    return NS(stop_reason="tool_use", content=[NS(type="tool_use", id=id_, name=name, input=inp)])


def a_text(t):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=t)])


class ScriptedAnthropic:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw["messages"], default=lambda o: "<obj>")))
        return self.responses.pop(0)


def anthropic_agent(booking, *responses):
    client = ScriptedAnthropic(*responses)
    return Agent(Deps(booking, FakeLedger()), llm=AnthropicLLM(client=client)), client


def test_anthropic_loop_runs_tool_then_returns_text(booking):
    agent, client = anthropic_agent(booking, a_tool_use("t1", "check_availability", **CHECK_ARGS),
                                    a_text("That one's open. Want it?"))
    assert agent.reply(agent.new_session("s1"), "Is 3:30 Thursday open?") == "That one's open. Want it?"
    result = client.calls[1][-1]["content"][0]  # the tool result was fed back to the model
    assert result["type"] == "tool_result" and json.loads(result["content"])["preferred_available"] is True


def test_tool_crash_becomes_error_result_not_exception(booking, monkeypatch):
    import app.agent as agent_mod

    monkeypatch.setattr(agent_mod, "run_tool", lambda *a, **k: 1 / 0)
    agent, client = anthropic_agent(booking, a_tool_use("t1", "check_availability", date="2026-11-05",
                                                        service="express_wash"), a_text("Sorry, something went wrong."))
    assert agent.reply(agent.new_session("s"), "hi").startswith("Sorry")
    assert json.loads(client.calls[1][-1]["content"][0]["content"])["error"] == "internal"


def test_step_cap_prevents_infinite_tool_loop(booking):
    loop = [a_tool_use(f"t{i}", "check_availability", date="2026-11-05", service="express_wash") for i in range(20)]
    agent, _ = anthropic_agent(booking, *loop)
    assert "call you back" in agent.reply(agent.new_session("s"), "hi")


# ---------------- Gemini ----------------
def g_call(name, **args):
    return NS(candidates=[NS(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name=name, args=args))]))])


def g_text(t):
    return NS(candidates=[NS(content=types.Content(role="model", parts=[types.Part(text=t)]))])


class ScriptedGemini:
    def __init__(self, *responses):
        self.responses, self.seen = list(responses), []
        self.models = NS(generate_content=self._generate)

    def _generate(self, **kw):
        self.seen.append(list(kw["contents"]))  # snapshot: the history list keeps growing afterwards
        return self.responses.pop(0)


def gemini_agent(booking, *responses):
    client = ScriptedGemini(*responses)
    return Agent(Deps(booking, FakeLedger()), llm=GeminiLLM(client=client, model="test")), client


def test_gemini_loop_runs_tool_then_returns_text(booking):
    agent, client = gemini_agent(booking, g_call("check_availability", **CHECK_ARGS), g_text("That one's open."))
    assert agent.reply(agent.new_session("s1"), "Is 3:30 Thursday open?") == "That one's open."
    last = client.seen[1][-1]  # what the model received on its second call
    assert last.role == "user"
    response = last.parts[0].function_response
    assert response.name == "check_availability" and response.response["result"]["preferred_available"] is True


def test_gemini_empty_answer_is_handled(booking):
    agent, _ = gemini_agent(booking, NS(candidates=[]))
    assert "say that again" in agent.reply(agent.new_session("s"), "hi")


def test_gemini_tool_schemas_are_accepted_by_the_sdk(booking):
    llm = GeminiLLM(client=NS(), model="test")
    names = [d.name for d in llm._tools[0].function_declarations]
    assert "book_appointment" in names and "handoff_to_human" in names


def test_system_prompt_contains_date_and_handoff_rules():
    p = system_prompt(NOW)
    assert "Monday, November 2, 2026" in p and "charge" in p and "Sunday: closed" in p


# ---------------- retries and failures ----------------
def test_gemini_retries_when_busy_then_succeeds(booking, monkeypatch):
    from google.genai import errors

    monkeypatch.setattr("app.llm.time.sleep", lambda s: None)
    calls = []

    def flaky(**kw):
        calls.append(1)
        if len(calls) < 3:
            raise errors.ServerError(503, {"error": {"message": "busy"}})
        return g_text("Thanks for calling.")

    client = NS(models=NS(generate_content=flaky))
    agent = Agent(Deps(booking, FakeLedger()), llm=GeminiLLM(client=client, model="test"))
    assert agent.reply(agent.new_session("s"), "hi") == "Thanks for calling." and len(calls) == 3


def test_model_outage_gives_a_polite_answer_not_a_crash(booking):
    class Down:
        def user_message(self, t):
            return t

        def complete(self, history, system):
            raise RuntimeError("down")

    agent = Agent(Deps(booking, FakeLedger()), llm=Down())
    session = agent.new_session("s")
    assert "trouble" in agent.reply(session, "hi") and session.messages == []


def test_prompt_tells_the_model_to_check_the_calendar_for_closures():
    p = system_prompt(NOW)
    assert "special_closure" in p and "closure_reason" in p and "fully_booked" in p


def test_thinking_level_is_only_sent_when_configured(monkeypatch):
    from app import config as cfg

    monkeypatch.setattr(cfg, "GEMINI_THINKING_LEVEL", "")  # ignore whatever your real .env says
    seen = []
    client = NS(models=NS(generate_content=lambda **kw: seen.append(kw["config"]) or g_text("hi")))
    llm = GeminiLLM(client=client, model="test")
    llm.complete([llm.user_message("hello")], "sys")
    monkeypatch.setattr(cfg, "GEMINI_THINKING_LEVEL", "MINIMAL")
    llm.complete([llm.user_message("hello")], "sys")
    assert seen[0].thinking_config is None and seen[1].thinking_config.thinking_level == "MINIMAL"


# ---------------- Gemini streaming (voice) ----------------
def g_chunk(*parts):
    return NS(candidates=[NS(content=types.Content(role="model", parts=list(parts)))])


def text_part(t):
    return types.Part(text=t)


def call_part(name, **args):
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


def stream_agent(booking, streams, monkeypatch, normal=()):
    from app import config as cfg

    monkeypatch.setattr(cfg, "GEMINI_STREAM", True)
    streams, normal = list(streams), list(normal)

    def gen(**kw):
        s = streams.pop(0)
        if isinstance(s, Exception):
            raise s
        return iter(s)

    client = NS(models=NS(generate_content_stream=gen, generate_content=lambda **kw: normal.pop(0)))
    return Agent(Deps(booking, FakeLedger()), llm=GeminiLLM(client=client, model="test"))


def test_streaming_speaks_words_as_they_arrive_without_repeating_them(booking, monkeypatch):
    agent = stream_agent(booking, [[g_chunk(text_part("We open ")), g_chunk(text_part("at nine."))]], monkeypatch)
    session = agent.new_session("s")
    assert list(agent.stream(session, "hours?", interim=True)) == ["We open ", "at nine."]
    assert session.messages[-1].parts[0].text == "We open " and len(session.messages[-1].parts) == 2


def test_streaming_tool_turn_says_filler_once_then_the_final_answer(booking, monkeypatch):
    agent = stream_agent(booking, [[g_chunk(call_part("check_availability", **CHECK_ARGS))],
                                   [g_chunk(text_part("That one's open."))]], monkeypatch)
    out = list(agent.stream(agent.new_session("s"), "3:30 Thursday?", interim=True))
    assert out == ["Sure, let me check that for you. ", "That one's open."]


def test_streaming_falls_back_to_the_normal_call_if_it_fails_early(booking, monkeypatch):
    agent = stream_agent(booking, [RuntimeError("boom")], monkeypatch, normal=[g_text("Hello there.")])
    assert list(agent.stream(agent.new_session("s"), "hi", interim=True)) == ["Hello there."]


def test_text_chat_never_streams_even_when_enabled(booking, monkeypatch):
    agent = stream_agent(booking, [], monkeypatch, normal=[g_text("Plain answer.")])
    assert agent.reply(agent.new_session("s"), "hi") == "Plain answer."


# ---------------- OpenAI-compatible provider (Groq) ----------------
from app.llm import OpenAICompatLLM  # noqa: E402


def o_msg(content=None, calls=()):
    tcs = [NS(id=i, function=NS(name=n, arguments=json.dumps(a))) for i, n, a in calls]
    return NS(choices=[NS(message=NS(content=content, tool_calls=tcs or None))])


class ScriptedOpenAI:
    def __init__(self, *responses):
        self.responses, self.sent = list(responses), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.sent.append(json.loads(json.dumps(kw["messages"], default=str)))
        return self.responses.pop(0)


def test_openai_compatible_sends_reasoning_effort_and_survives_an_empty_reply(booking, monkeypatch):
    from app import config as cfg

    monkeypatch.setattr(cfg, "GROQ_REASONING_EFFORT", "low")
    seen = []
    client = NS(chat=NS(completions=NS(create=lambda **kw: seen.append(kw) or o_msg(""))))
    out = OpenAICompatLLM(client=client, model="m").complete([{"role": "user", "content": "hi"}], "sys")
    assert seen[0]["extra_body"] == {"reasoning_effort": "low"}
    assert seen[0]["max_tokens"] == 500
    assert out.text.startswith("Sorry") and out.tool_calls == []


def test_openai_compatible_retries_a_busy_service(booking, monkeypatch):
    monkeypatch.setattr("app.llm.time.sleep", lambda s: None)
    calls = []

    def create(**kw):
        calls.append(1)
        if len(calls) < 3:
            raise type("Busy", (Exception,), {"status_code": 503})()
        return o_msg("ok then")

    client = NS(chat=NS(completions=NS(create=create)))
    assert OpenAICompatLLM(client=client, model="m").complete([{"role": "user", "content": "hi"}], "s").text == "ok then"
    assert len(calls) == 3

def test_openai_compatible_does_not_retry_rate_limit(monkeypatch):
    calls = []

    def create(**kw):
        calls.append(1)
        raise type("RateLimited", (Exception,), {"status_code": 429})()

    client = NS(chat=NS(completions=NS(create=create)))

    with pytest.raises(Exception):
        OpenAICompatLLM(client=client, model="m").complete(
            [{"role": "user", "content": "hi"}], "s"
        )

    assert len(calls) == 1

def test_openai_compatible_loop_runs_tool_then_returns_text(booking):
    client = ScriptedOpenAI(o_msg(calls=[("call_1", "check_availability", CHECK_ARGS)]), o_msg("That one's open."))
    agent = Agent(Deps(booking, FakeLedger()), llm=OpenAICompatLLM(client=client, model="m"))
    assert agent.reply(agent.new_session("s"), "Is 3:30 Thursday open?") == "That one's open."
    second = client.sent[1]
    assert second[0]["role"] == "system" and second[-2]["tool_calls"][0]["id"] == "call_1"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "call_1"
    assert json.loads(second[-1]["content"])["preferred_available"] is True


def _err(status, retry_after=None):
    resp = NS(headers={"retry-after": str(retry_after)}) if retry_after is not None else None
    return type("E", (Exception,), {"status_code": status, "response": resp})()


def test_openai_compatible_retries_a_garbled_tool_call(monkeypatch):
    monkeypatch.setattr("app.llm.time.sleep", lambda s: None)
    calls = []

    def create(**kw):
        calls.append(1)
        if len(calls) < 3:
            raise _err(400)  # "tool call validation failed: book_appointment<|channel|>commentary"
        return o_msg("fine")

    client = NS(chat=NS(completions=NS(create=create)))
    assert OpenAICompatLLM(client=client, model="m").complete([{"role": "user", "content": "hi"}], "s").text == "fine"
    assert len(calls) == 3


def test_openai_compatible_waits_out_a_short_rate_limit_but_not_a_long_one(monkeypatch):
    slept = []
    monkeypatch.setattr("app.llm.time.sleep", slept.append)
    calls = []

    def short(**kw):
        calls.append(1)
        if len(calls) == 1:
            raise _err(429, retry_after=2)
        return o_msg("ok")

    client = NS(chat=NS(completions=NS(create=short)))
    assert OpenAICompatLLM(client=client, model="m").complete([{"role": "user", "content": "hi"}], "s").text == "ok"
    assert slept == [2.0]

    def long(**kw):
        raise _err(429, retry_after=40)

    client = NS(chat=NS(completions=NS(create=long)))
    with pytest.raises(Exception):
        OpenAICompatLLM(client=client, model="m").complete([{"role": "user", "content": "hi"}], "s")
