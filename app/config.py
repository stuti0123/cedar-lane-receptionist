"""Environment-driven settings. Copy .env.example to .env and fill it in."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv

    load_dotenv()
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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
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
    else:
        problems.append(f"LLM_PROVIDER must be 'gemini' or 'anthropic', not '{LLM_PROVIDER}'")
    if not GOOGLE_CALENDAR_ID:
        problems.append("GOOGLE_CALENDAR_ID is not set (put it in .env)")
    if not GOOGLE_SHEET_ID:
        problems.append("GOOGLE_SHEET_ID is not set (put it in .env)")
    if not Path(GOOGLE_CREDENTIALS_FILE).is_file():
        problems.append(f"Google credentials file not found: {GOOGLE_CREDENTIALS_FILE}")
    if problems:
        raise RuntimeError("Configuration problems:\n  - " + "\n  - ".join(problems))
