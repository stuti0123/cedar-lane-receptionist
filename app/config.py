"""Environment-driven settings. Copy .env.example to .env and fill it in."""
import os
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
