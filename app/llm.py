"""Everything specific to ONE AI provider lives in this file, so the rest of the project (the agent loop,
tools, booking rules) is identical whichever provider you pick. Choose with LLM_PROVIDER in .env.

Each provider class does the same four things:
  user_message(text)            -> a message in that provider's format
  complete(history, system)     -> ask the model for its next step, returns a Turn
  tool_results_message(results) -> a message carrying tool results back to the model
"""
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from . import config
from .tools import TOOL_SPECS

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str                  # what the model said (may be empty when it only calls tools)
    tool_calls: list[ToolCall]  # actions it wants run
    message: Any               # the model's message, in provider format, to append to the history


# ---------------------------------------------------------------------------------------------
class AnthropicLLM:
    def __init__(self, client=None, model: str | None = None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.client, self.model = client, model or config.ANTHROPIC_MODEL

    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def complete(self, history: list, system: str) -> Turn:
        resp = self.client.messages.create(
            model=self.model, max_tokens=1024, system=system, tools=TOOL_SPECS, messages=history
        )
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return Turn(text, calls, {"role": "assistant", "content": resp.content})

    def tool_results_message(self, results: list[tuple[ToolCall, dict]]) -> dict:
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c.id, "content": json.dumps(out), "is_error": not out.get("ok", False)}
            for c, out in results
        ]}


# ---------------------------------------------------------------------------------------------
class GeminiLLM:
    def __init__(self, client=None, model: str | None = None):
        from google import genai
        from google.genai import types

        self.types = types
        self.client = client or genai.Client()  # reads GEMINI_API_KEY (or GOOGLE_API_KEY)
        self.model = model or config.GEMINI_MODEL
        self._tools = [types.Tool(function_declarations=[
            types.FunctionDeclaration(name=t["name"], description=t["description"],
                                      parameters_json_schema=t["input_schema"])
            for t in TOOL_SPECS
        ])]

    def _generate(self, **kwargs):
        """Gemini's free tier sometimes answers 503 (busy) or 429 (rate limit). Wait a little and retry."""
        from google.genai import errors

        for attempt in range(1, 5):
            try:
                return self.client.models.generate_content(**kwargs)
            except errors.APIError as e:
                if e.code not in (429, 500, 503) or attempt == 4:
                    raise
                log.warning("Gemini returned %s, retrying (%s/3)", e.code, attempt)
                time.sleep(2 * attempt)

    def user_message(self, text: str):
        return self.types.Content(role="user", parts=[self.types.Part(text=text)])

    def complete(self, history: list, system: str) -> Turn:
        t = self.types
        resp = self._generate(
            model=self.model, contents=history,
            config=t.GenerateContentConfig(
                system_instruction=system, tools=self._tools,
                # we run the tools ourselves (so every action goes through booking.py and gets logged)
                automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        content = resp.candidates[0].content if resp.candidates else None
        if content is None or not content.parts:  # e.g. the answer was blocked by a safety filter
            sorry = "Sorry, could you say that again?"
            return Turn(sorry, [], t.Content(role="model", parts=[t.Part(text=sorry)]))
        calls = [ToolCall(f"{p.function_call.name}-{i}", p.function_call.name, dict(p.function_call.args or {}))
                 for i, p in enumerate(content.parts) if p.function_call]
        text = "".join(p.text for p in content.parts if p.text and not p.thought).strip()
        # the model's own message goes back into the history untouched (Gemini needs its hidden
        # "thought signatures" returned exactly as received)
        return Turn(text, calls, content)

    def tool_results_message(self, results: list[tuple[ToolCall, dict]]):
        t = self.types
        return t.Content(role="user", parts=[
            t.Part.from_function_response(name=c.name, response={"result": out}) for c, out in results
        ])


# ---------------------------------------------------------------------------------------------
def build_llm():
    if config.LLM_PROVIDER == "gemini":
        return GeminiLLM()
    if config.LLM_PROVIDER == "anthropic":
        return AnthropicLLM()
    raise RuntimeError(f"Unknown LLM_PROVIDER '{config.LLM_PROVIDER}' (use 'gemini' or 'anthropic')")
