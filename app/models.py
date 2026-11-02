"""Small data types shared by the logic and the Google adapters."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Busy:
    """A block of time that occupies a bay. Any calendar event counts, ours or not."""

    start: datetime
    end: datetime
    event_id: str | None = None


@dataclass
class CallContext:
    """State for one conversation (= one call). Filled in as the caller reveals who they are."""

    session_id: str
    phone: str | None = None  # 10 digits, once known
    name: str | None = None
    vehicle: str | None = None
    call_counted: bool = False  # so 'Total Calls' goes up once per conversation, not once per log line


@dataclass(frozen=True)
class Appt:
    """An appointment created by this system (it carries a phone + service)."""

    event_id: str
    start: datetime
    end: datetime
    service_key: str
    phone: str  # 10 digits
    summary: str = ""
