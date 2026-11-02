"""Booking service: the only place that decides what is allowed and talks to the calendar.
The LLM never touches the calendar directly; it calls tools, tools call this.

The calendar is injected (see CalendarPort), so this file is tested with an in-memory fake."""
import threading
from datetime import date, datetime, time, timedelta
from typing import Callable, Protocol

from . import shop
from .availability import bookable_reason, open_starts
from .config import SHOP_TZ
from .models import Appt, Busy
from .phone import format_phone, normalize_phone

SEARCH_DAYS_AHEAD = 14  # how far we look for "next open day"

ERROR_TEXT = {
    "closed": "We're closed that day.",
    "invalid_time": "That isn't a valid start time (starts are every 30 minutes, and the service must finish before closing).",
    "too_soon": "That time is in the past or too soon (we need at least an hour's notice).",
    "taken": "That time is already booked.",
}


class CalendarPort(Protocol):
    def list_busy(self, start: datetime, end: datetime) -> list[Busy]: ...
    def create_event(self, *, start, end, summary, description, phone, service_key) -> str: ...
    def get_event(self, event_id: str) -> Appt | None: ...
    def move_event(self, event_id: str, start: datetime, end: datetime) -> None: ...
    def delete_event(self, event_id: str) -> None: ...
    def append_note(self, event_id: str, text: str) -> None: ...
    def find_by_phone(self, phone: str, from_dt: datetime) -> list[Appt]: ...


class BookingError(Exception):
    """A business-rule failure. The message is safe to relay to the caller."""

    def __init__(self, code: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra or {}


def label(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p")


def slot(dt: datetime) -> dict:
    return {"time": dt.strftime("%H:%M"), "label": label(dt)}


class Booking:
    def __init__(self, calendar: CalendarPort, tz=SHOP_TZ, clock: Callable[[], datetime] | None = None):
        self.cal = calendar
        self.tz = tz
        self.now = clock or (lambda: datetime.now(tz))
        # Google Calendar has no "insert if free", so check+insert is serialized in-process.
        # Fine for one server process; see README for the multi-process caveat.
        self._lock = threading.Lock()

    # ---------- parsing / helpers ----------
    def _service(self, key: str) -> shop.Service:
        if key not in shop.SERVICES:
            raise BookingError("unknown_service", f"Unknown service. Valid: {', '.join(shop.SERVICES)}")
        return shop.SERVICES[key]

    def _day(self, day: str) -> date:
        try:
            d = date.fromisoformat(day)
        except ValueError:
            raise BookingError("bad_date", "Date must be YYYY-MM-DD.")
        if d < self.now().date():
            raise BookingError("past_date", "That date has already passed.")
        return d

    def _start(self, day: str, hhmm: str) -> datetime:
        d = self._day(day)
        try:
            h, m = hhmm.split(":")
            return datetime.combine(d, time(int(h), int(m)), tzinfo=self.tz)
        except ValueError:
            raise BookingError("bad_time", "Time must be 24-hour HH:MM.")

    def _busy(self, day: date, days: int = 1) -> list[Busy]:
        start = datetime.combine(day, time(0), tzinfo=self.tz)
        return self.cal.list_busy(start, start + timedelta(days=days))

    def _phone(self, raw: str) -> str:
        try:
            return normalize_phone(raw)
        except ValueError as e:
            raise BookingError("bad_phone", str(e))

    def _own(self, event_id: str, phone: str) -> Appt:
        """Fetch an appointment and make sure it belongs to this caller (by phone)."""
        digits = self._phone(phone)
        appt = self.cal.get_event(event_id)
        if appt is None or appt.phone != digits:
            # same message for 'doesn't exist' and 'not yours' so ids can't be probed
            raise BookingError("not_found", "No matching appointment for that phone number.")
        return appt

    def _alternatives(self, start: datetime, svc: shop.Service) -> dict:
        busy = self._busy(start.date(), SEARCH_DAYS_AHEAD + 1)
        same_day = open_starts(start.date(), svc.minutes, busy, self.now(), self.tz)
        near = sorted(sorted(same_day, key=lambda s: abs(s - start))[:3])
        return {"same_day": [slot(s) for s in near], "next_open_day": self._next_open_day(start.date(), svc, busy)}

    def _next_open_day(self, after: date, svc, busy) -> dict | None:
        for i in range(1, SEARCH_DAYS_AHEAD + 1):
            d = after + timedelta(days=i)
            starts = open_starts(d, svc.minutes, busy, self.now(), self.tz)
            if starts:
                return {"date": d.isoformat(), "weekday": d.strftime("%A"), "first_open": slot(starts[0])}
        return None

    def _fail(self, reason: str, start: datetime, svc: shop.Service):
        raise BookingError(reason, ERROR_TEXT[reason], self._alternatives(start, svc))

    # ---------- public operations ----------
    def check(self, day: str, service_key: str, preferred: str | None = None) -> dict:
        svc = self._service(service_key)
        d = self._day(day)
        busy = self._busy(d, SEARCH_DAYS_AHEAD + 1)
        out = {"date": d.isoformat(), "weekday": d.strftime("%A"), "service": svc.name, "minutes": svc.minutes}
        if shop.OPEN_HOURS.get(d.weekday()) is None:
            return {**out, "closed": True, "next_open_day": self._next_open_day(d, svc, busy)}
        starts = open_starts(d, svc.minutes, busy, self.now(), self.tz)
        out |= {"closed": False, "open_slots": [slot(s) for s in starts]}
        if preferred:
            want = self._start(day, preferred)
            out["preferred_time"] = label(want)
            out["preferred_available"] = want in starts
            near = sorted(sorted((s for s in starts if s != want), key=lambda s: abs(s - want))[:3])
            out["nearest_alternatives"] = [slot(s) for s in near]
        if not starts:
            out["next_open_day"] = self._next_open_day(d, svc, busy)
        return out

    def book(self, day, time_str, service_key, name, phone, vehicle=None, notes=None) -> dict:
        svc = self._service(service_key)
        name = (name or "").strip()
        if not name:
            raise BookingError("missing_name", "Need the caller's name.")
        digits = self._phone(phone)
        start = self._start(day, time_str)
        end = start + timedelta(minutes=svc.minutes)
        with self._lock:
            # re-check right before writing: availability may have changed since check_availability
            reason = bookable_reason(start, svc.minutes, self._busy(start.date()), self.now())
            if reason:
                self._fail(reason, start, svc)
            if any(a.start == start for a in self.cal.find_by_phone(digits, self.now())):
                raise BookingError("duplicate", "This caller already has an appointment at that time.")
            desc = f"Phone: {format_phone(digits)}\nVehicle: {vehicle or 'n/a'}\nNotes: {notes or '-'}\nBooked by AI receptionist"
            event_id = self.cal.create_event(
                start=start, end=end, summary=f"{svc.name} - {name}",
                description=desc, phone=digits, service_key=svc.key,
            )
        return {
            "event_id": event_id, "service": svc.name, "date": start.date().isoformat(),
            "weekday": start.strftime("%A"), "start": label(start), "end": label(end),
            "starting_price": svc.price, "name": name, "phone": format_phone(digits),
        }

    def find(self, phone: str) -> list[dict]:
        digits = self._phone(phone)
        appts = self.cal.find_by_phone(digits, self.now())
        return [
            {"event_id": a.event_id, "service": shop.SERVICES[a.service_key].name,
             "date": a.start.date().isoformat(), "weekday": a.start.strftime("%A"), "start": label(a.start)}
            for a in sorted(appts, key=lambda a: a.start)
        ]

    def reschedule(self, event_id, phone, day, time_str) -> dict:
        appt = self._own(event_id, phone)
        svc = shop.SERVICES[appt.service_key]
        start = self._start(day, time_str)
        end = start + timedelta(minutes=svc.minutes)
        if start == appt.start:
            raise BookingError("same_time", "That's already the appointment time.")
        with self._lock:
            reason = bookable_reason(start, svc.minutes, self._busy(start.date()), self.now(), ignore_event_id=event_id)
            if reason:
                self._fail(reason, start, svc)
            self.cal.move_event(event_id, start, end)
        return {
            "service": svc.name, "old": f"{appt.start.strftime('%A %Y-%m-%d')} {label(appt.start)}",
            "new": f"{start.strftime('%A %Y-%m-%d')} {label(start)}",
        }

    def cancel(self, event_id, phone) -> dict:
        appt = self._own(event_id, phone)
        self.cal.delete_event(event_id)
        return {"service": shop.SERVICES[appt.service_key].name,
                "was": f"{appt.start.strftime('%A %Y-%m-%d')} {label(appt.start)}"}

    def mark_late(self, event_id, phone, minutes_late: int) -> dict:
        appt = self._own(event_id, phone)
        if appt.start.date() != self.now().date():
            raise BookingError("not_today", "That appointment isn't today.")
        if not 1 <= minutes_late <= 240:
            raise BookingError("bad_minutes", "minutes_late must be between 1 and 240.")
        self.cal.append_note(event_id, f"Caller reported at {label(self.now())} that they are ~{minutes_late} min late.")
        return {
            "appointment": label(appt.start), "minutes_late": minutes_late,
            "within_grace": minutes_late <= shop.LATE_GRACE_MIN, "grace_minutes": shop.LATE_GRACE_MIN,
        }
