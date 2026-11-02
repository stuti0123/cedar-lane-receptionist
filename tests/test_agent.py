"""The LLM loop, with a scripted fake client (no network, no API key). It checks the plumbing:
tool calls are executed, results go back to the model, and the final text is returned."""
import json
from types import SimpleNamespace as NS

from app.agent import Agent, system_prompt
from app.tools import Deps
from fakes import FakeLedger
from conftest import NOW


def tool_use(id_, name, **inp):
    return NS(stop_reason="tool_use", content=[NS(type="tool_use", id=id_, name=name, input=inp)])


def text(t):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=t)])


class ScriptedClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw["messages"], default=lambda o: "<obj>")))
        return self.responses.pop(0)


def test_loop_runs_tool_then_returns_text(booking):
    client = ScriptedClient(
        tool_use("t1", "check_availability", date="2026-11-05", service="interior_detail_wash", preferred_time="15:30"),
        text("That one's open. Want it?"),
    )
    agent = Agent(Deps(booking, FakeLedger()), client=client)
    session = agent.new_session("s1")
    assert agent.reply(session, "Is 3:30 Thursday open?") == "That one's open. Want it?"
    # the tool result was fed back to the model as the last user message of the 2nd call
    result = client.calls[1][-1]["content"][0]
    assert result["type"] == "tool_result" and json.loads(result["content"])["preferred_available"] is True


def test_tool_crash_becomes_error_result_not_exception(booking, monkeypatch):
    import app.agent as agent_mod

    monkeypatch.setattr(agent_mod, "run_tool", lambda *a, **k: 1 / 0)
    client = ScriptedClient(tool_use("t1", "check_availability", date="2026-11-05", service="express_wash"),
                            text("Sorry, something went wrong."))
    agent = Agent(Deps(booking, FakeLedger()), client=client)
    assert agent.reply(agent.new_session("s"), "hi").startswith("Sorry")
    assert json.loads(client.calls[1][-1]["content"][0]["content"])["error"] == "internal"


def test_step_cap_prevents_infinite_tool_loop(booking):
    loop = [tool_use(f"t{i}", "check_availability", date="2026-11-05", service="express_wash") for i in range(20)]
    agent = Agent(Deps(booking, FakeLedger()), client=ScriptedClient(*loop))
    assert "call you back" in agent.reply(agent.new_session("s"), "hi")


def test_system_prompt_contains_date_and_handoff_rules():
    p = system_prompt(NOW)
    assert "Monday, November 2, 2026" in p and "charge" in p and "Sunday: closed" in p
