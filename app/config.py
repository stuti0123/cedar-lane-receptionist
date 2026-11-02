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
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")

# LLM
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
MAX_TOOL_STEPS = 8  # safety cap on tool calls per user message


def validate() -> None:
    """Fail at startup with ONE clear message, instead of failing on the first caller's request."""
    problems = []
    if not os.getenv("ANTHROPIC_API_KEY"):
        problems.append("ANTHROPIC_API_KEY is not set (put it in .env)")
    if not GOOGLE_CALENDAR_ID:
        problems.append("GOOGLE_CALENDAR_ID is not set (put it in .env)")
    if not GOOGLE_SHEET_ID:
        problems.append("GOOGLE_SHEET_ID is not set (put it in .env)")
    if not Path(GOOGLE_CREDENTIALS_FILE).is_file():
        problems.append(f"Google credentials file not found: {GOOGLE_CREDENTIALS_FILE}")
    if problems:
        raise RuntimeError("Configuration problems:\n  - " + "\n  - ".join(problems))
