"""Environment-driven settings. Copy .env.example to .env and fill it in."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv

    load_dotenv(override=True)
except ImportError:  # tests don't need dotenv
    pass

SHOP_TZ = ZoneInfo(os.getenv("SHOP_TZ", "America/Los_Angeles"))

# Google
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")  # only used for OAuth sign-in
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")

# LLM: pick a provider with LLM_PROVIDER = gemini (free tier available) or anthropic (paid)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
# Optional speed knob for voice: MINIMAL, LOW, MEDIUM or HIGH. Empty = the model's default (no change).
# Voice speed: speak the model's words as they arrive instead of waiting for the whole answer. 1 = on.
GEMINI_STREAM = os.getenv("GEMINI_STREAM", "").strip().lower() in ("1", "true", "yes", "on")
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "").strip().upper()
# Groq (OpenAI-compatible, fast): LLM_PROVIDER=groq. Pick a current tool-calling model from console.groq.com/docs/models
# Voice: wait this long before starting the model, so a request Vapi abandons because the caller kept talking
# costs nothing (Vapi re-sends on every speech fragment). 0 turns it off.
VAPI_DEBOUNCE_SECONDS = float(os.getenv("VAPI_DEBOUNCE_SECONDS", "0.5"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
# gpt-oss models "think" before answering; "low" keeps voice replies quick. Empty = don't send the setting.
GROQ_REASONING_EFFORT = os.getenv("GROQ_REASONING_EFFORT", "low" if "gpt-oss" in GROQ_MODEL else "").strip().lower()
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
# Phase 2: secret that Vapi must send as "Authorization: Bearer <secret>". Set it when the server is public.
VAPI_SECRET = os.getenv("VAPI_SECRET", "")
MAX_TOOL_STEPS = 8  # safety cap on tool calls per user message


def validate() -> None:
    """Fail at startup with ONE clear message, instead of failing on the first caller's request."""
    problems = []
    if LLM_PROVIDER == "gemini":
        if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
            problems.append("GEMINI_API_KEY is not set (put it in .env; get one at aistudio.google.com/apikey)")
    elif LLM_PROVIDER == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            problems.append("ANTHROPIC_API_KEY is not set (put it in .env)")
    elif LLM_PROVIDER == "groq":
        if not GROQ_API_KEY:
            problems.append("GROQ_API_KEY is not set (put it in .env)")
    else:
        problems.append(
            f"LLM_PROVIDER must be 'gemini', 'anthropic' or 'groq', not '{LLM_PROVIDER}'"
        )
    if not GOOGLE_CALENDAR_ID:
        problems.append("GOOGLE_CALENDAR_ID is not set (put it in .env)")
    if not GOOGLE_SHEET_ID:
        problems.append("GOOGLE_SHEET_ID is not set (put it in .env)")
    if not Path(GOOGLE_CREDENTIALS_FILE).is_file():
        problems.append(f"Google credentials file not found: {GOOGLE_CREDENTIALS_FILE}")
    if problems:
        raise RuntimeError("Configuration problems:\n  - " + "\n  - ".join(problems))
