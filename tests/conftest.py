import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeCalendar  # noqa: E402
from app.booking import Booking  # noqa: E402
from app.config import SHOP_TZ  # noqa: E402

# "Now" is Monday 2 Nov 2026, 12:30 pm shop time. Thursday = 5 Nov, Sunday = 8 Nov.
NOW = datetime(2026, 11, 2, 12, 30, tzinfo=SHOP_TZ)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """November 2026 helper: at(5, 15, 30) -> Thu 5 Nov 3:30 pm."""
    return datetime(2026, 11, day, hour, minute, tzinfo=SHOP_TZ)


@pytest.fixture
def cal():
    return FakeCalendar()


@pytest.fixture
def booking(cal):
    return Booking(cal, tz=SHOP_TZ, clock=lambda: NOW)
