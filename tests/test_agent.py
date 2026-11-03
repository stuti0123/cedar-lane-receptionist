"""The conversation loop, with scripted fake AI clients (no network, no API key). It checks the plumbing:
tool calls are executed, results go back to the model, and the final text is returned. Both providers."""
import json
from types import SimpleNamespace as NS

from google.genai import types

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
