"""Pure scheduling logic: no Google, no LLM, no I/O. This is the part that must be correct,
so it is small and fully unit-tested (tests/test_availability.py)."""
from datetime import date, datetime, timedelta

from . import shop
from .models import Busy


def hours_for(day: date):
    return shop.OPEN_HOURS.get(day.weekday())


def peak_overlap(start: datetime, end: datetime, busy: list[Busy]) -> int:
    """Max number of busy blocks active at the same instant anywhere inside [start, end).

    Counting overlapping events is NOT enough when there are several bays: two events
    that overlap my slot but not each other still leave a bay free. So we check the
    concurrency at the start of the slot and at every moment another block begins."""
    relevant = [b for b in busy if b.start < end and b.end > start]
    points = {start} | {b.start for b in relevant if b.start > start}
    return max(sum(1 for b in relevant if b.start <= t < b.end) for t in points)


def grid_starts(day: date, minutes: int, tz) -> list[datetime]:
    """Every legal start time that day: on the step grid, finishing before close."""
    hours = hours_for(day)
    if not hours:
        return []
    t = datetime.combine(day, hours[0], tzinfo=tz)
    close = datetime.combine(day, hours[1], tzinfo=tz)
    out = []
    while t + timedelta(minutes=minutes) <= close:
        out.append(t)
        t += timedelta(minutes=shop.SLOT_STEP_MIN)
    return out


def bookable_reason(
    start: datetime,
    minutes: int,
    busy: list[Busy],
    now: datetime,
    bays: int | None = None,
    ignore_event_id: str | None = None,
) -> str | None:
    """None if the slot can be booked, else why not: closed | invalid_time | too_soon | taken.

    ignore_event_id lets a reschedule move an appointment into a slot that overlaps its
    own current time without it conflicting with itself."""
    bays = shop.BAYS if bays is None else bays
    if hours_for(start.date()) is None:
        return "closed"
    if start not in grid_starts(start.date(), minutes, start.tzinfo):
        return "invalid_time"
    if start < now + timedelta(minutes=shop.MIN_NOTICE_MIN):
        return "too_soon"
    others = [b for b in busy if ignore_event_id is None or b.event_id != ignore_event_id]
    if peak_overlap(start, start + timedelta(minutes=minutes), others) >= bays:
        return "taken"
    return None


def open_starts(day, minutes, busy, now, tz, bays=None, ignore_event_id=None) -> list[datetime]:
    return [
        s
        for s in grid_starts(day, minutes, tz)
        if bookable_reason(s, minutes, busy, now, bays, ignore_event_id) is None
    ]
