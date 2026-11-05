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
        self.client = client or genai.Client(api_key=config.GEMINI_API_KEY)  # reads GEMINI_API_KEY (or GOOGLE_API_KEY)
        self.model = model or config.GEMINI_MODEL
        self._tools = [types.Tool(function_declarations=[
            types.FunctionDeclaration(name=t["name"], description=t["description"],
                                      parameters_json_schema=t["input_schema"])
            for t in TOOL_SPECS
        ])]


    def _generate(self, **kwargs):
        """Retry temporary Gemini server errors, but never retry quota/rate-limit errors."""
        from google.genai import errors

        for attempt in range(1, 4):
            try:
                return self.client.models.generate_content(**kwargs)

            except errors.APIError as e:
                # 429 means quota/rate limit. Retrying won't fix an exhausted quota.
                if e.code == 429:
                    log.error("Gemini quota/rate limit reached. Not retrying.")
                    raise

                # Retry only temporary server errors.
                if e.code not in (500, 503) or attempt == 3:
                    raise

                log.warning(
                    "Gemini returned %s. Retrying (%s/2)...",
                    e.code,
                    attempt
                )
                time.sleep(2 * attempt)


    def user_message(self, text: str):
        return self.types.Content(role="user", parts=[self.types.Part(text=text)])

    def _config(self, system: str):
        t = self.types
        return t.GenerateContentConfig(
            system_instruction=system, tools=self._tools,
            # we run the tools ourselves (so every action goes through booking.py and gets logged)
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
            **({"thinking_config": t.ThinkingConfig(thinking_level=config.GEMINI_THINKING_LEVEL)}
               if config.GEMINI_THINKING_LEVEL else {}),
        )

    def _turn_from(self, parts: list) -> Turn:
        t = self.types
        if not parts:  # e.g. the answer was blocked by a safety filter
            sorry = "Sorry, could you say that again?"
            return Turn(sorry, [], t.Content(role="model", parts=[t.Part(text=sorry)]))
        calls = [ToolCall(f"{p.function_call.name}-{i}", p.function_call.name, dict(p.function_call.args or {}))
                 for i, p in enumerate(parts) if p.function_call]
        text = "".join(p.text for p in parts if p.text and not p.thought).strip()
        # the model's own parts go back into the history untouched (Gemini needs its hidden
        # "thought signatures" returned exactly as received)
        return Turn(text, calls, t.Content(role="model", parts=parts))

    def complete(self, history: list, system: str) -> Turn:
        resp = self._generate(model=self.model, contents=history, config=self._config(system))
        content = resp.candidates[0].content if resp.candidates else None
        return self._turn_from(list(content.parts) if content and content.parts else [])

    def complete_stream(self, history: list, system: str):
        """Voice: like complete(), but yields the spoken words the moment they arrive. The generator's
        return value is the finished Turn. If streaming fails before anything was said, fall back to complete()."""
        parts, spoke = [], False
        try:
            for chunk in self.client.models.generate_content_stream(
                    model=self.model, contents=history, config=self._config(system)):
                content = chunk.candidates[0].content if chunk.candidates else None
                for p in (content.parts if content and content.parts else []):
                    parts.append(p)
                    if p.text and not p.thought:
                        spoke = True
                        yield p.text
        except Exception:
            if spoke:
                raise
            log.exception("Gemini streaming failed before any words; using the normal call")
            turn = self.complete(history, system)
            if turn.text:
                yield turn.text  # the caller of this generator expects every spoken word to come through it
            return turn
        turn = self._turn_from(parts)
        if not parts:
            yield turn.text  # the "say that again" fallback
        return turn

    def tool_results_message(self, results: list[tuple[ToolCall, dict]]):
        t = self.types
        return t.Content(role="user", parts=[
            t.Part.from_function_response(name=c.name, response={"result": out}) for c, out in results
        ])


# ---------------------------------------------------------------------------------------------
class OpenAICompatLLM:
    """Any provider that speaks the OpenAI chat format. Used for Groq (very fast first words, free tier),
    which is what makes the voice agent snappy. Pick it with LLM_PROVIDER=groq and GROQ_API_KEY in .env."""

    TOOLS_BATCH = "_tool_results"

    def __init__(self, client=None, model: str | None = None):
        if client is None:
            from openai import OpenAI

            if not config.GROQ_API_KEY:
                raise RuntimeError("LLM_PROVIDER=groq needs GROQ_API_KEY in .env (free key: console.groq.com/keys)")
            client = OpenAI(base_url=config.GROQ_BASE_URL, api_key=config.GROQ_API_KEY, timeout=20, max_retries=0)
        self.client, self.model = client, model or config.GROQ_MODEL
        self._tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in TOOL_SPECS]

    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def _wire(self, history: list, system: str) -> list:
        """Our history -> the provider's message list (one 'tool' message per tool result)."""
        out = [{"role": "system", "content": system}]
        for m in history:
            if m.get("role") == self.TOOLS_BATCH:
                out += [{"role": "tool", "tool_call_id": cid, "content": json.dumps(res)} for cid, res in m["items"]]
            else:
                out.append(m)
        return out

    def complete(self, history: list, system: str) -> Turn:
        extra = {"reasoning_effort": config.GROQ_REASONING_EFFORT} if config.GROQ_REASONING_EFFORT else {}
        kwargs = dict(model=self.model, messages=self._wire(history, system), tools=self._tools,
              tool_choice="auto", temperature=0.2, max_tokens=500,
              **({"extra_body": extra} if extra else {}))
        for attempt in range(1, 4):  # these models occasionally produce a malformed tool call, or the service is busy
            try:
                resp = self.client.chat.completions.create(**kwargs)
                break
            except Exception as e:
                status = getattr(e, "status_code", None)
                if status == 429 or status not in (500, 502, 503) or attempt == 3:
                    raise
                log.warning("Groq returned %s, retrying (%s/2)", status, attempt)
                time.sleep(0.5 * attempt)
        choice = resp.choices[0]
        msg = choice.message
        if not msg.tool_calls and not (msg.content or "").strip():  # e.g. ran out of room while thinking
            sorry = "Sorry, could you say that again?"
            return Turn(sorry, [], {"role": "assistant", "content": sorry})
        raw_calls = msg.tool_calls or []
        calls = [ToolCall(c.id, c.function.name, json.loads(c.function.arguments or "{}")) for c in raw_calls]
        message: dict = {"role": "assistant", "content": msg.content or ""}
        if raw_calls:
            message["tool_calls"] = [{"id": c.id, "type": "function",
                                      "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                                     for c in raw_calls]
        return Turn((msg.content or "").strip(), calls, message)

    def tool_results_message(self, results: list[tuple[ToolCall, dict]]) -> dict:
        return {"role": self.TOOLS_BATCH, "items": [(c.id, out) for c, out in results]}


# ---------------------------------------------------------------------------------------------
def build_llm():
    if config.LLM_PROVIDER == "gemini":
        return GeminiLLM()
    if config.LLM_PROVIDER == "anthropic":
        return AnthropicLLM()
    if config.LLM_PROVIDER == "groq":
        return OpenAICompatLLM()
    raise RuntimeError(f"Unknown LLM_PROVIDER '{config.LLM_PROVIDER}' (use 'gemini', 'groq' or 'anthropic')")
