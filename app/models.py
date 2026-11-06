"""Small data types shared by the logic and the Google adapters."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Busy:
    """A block of time that occupies a bay. Any calendar event counts, ours or not."""

    start: datetime
    end: datetime
    event_id: str | None = None
    all_day: bool = False  # an all-day calendar event (holiday, staff training...) closes the whole day
    title: str = ""


@dataclass
class CallContext:
    """State for one conversation (= one call). Filled in as the caller reveals who they are."""

    session_id: str
    phone: str | None = None  # 10 digits, once known
    name: str | None = None
    vehicle: str | None = None
    call_counted: bool = False  # so 'Total Calls' goes up once per conversation, not once per log line
    # Voice calls mishear digits, so a booking is only made after the caller has heard the details read back:
    confirm_booking: bool = False          # on for voice calls
    turn: int = 0                          # how many caller messages so far
    pending_booking: tuple | None = None   # the booking details we asked the caller to confirm
    pending_turn: int = -1                 # the caller message on which we asked
    recent_user_texts: list = field(default_factory=list)  # what the caller actually said (to double-check phone digits)


@dataclass(frozen=True)
class Appt:
    """An appointment created by this system (it carries a phone + service)."""

    event_id: str
    start: datetime
    end: datetime
    service_key: str
    phone: str  # 10 digits
    summary: str = ""
