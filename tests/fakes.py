"""In-memory stand-ins for Google Calendar / Sheets so the logic can be tested offline."""
import itertools
from datetime import datetime

from app.models import Appt, Busy


class FakeCalendar:
    def __init__(self):
        self.events: dict[str, dict] = {}
        self._ids = itertools.count(1)

    def add_blocker(self, start: datetime, end: datetime, phone=None, service_key=None, all_day=False, title="") -> str:
        eid = f"ev{next(self._ids)}"
        self.events[eid] = dict(start=start, end=end, phone=phone, service_key=service_key, notes=[],
                                all_day=all_day, title=title)
        return eid

    # --- CalendarPort ---
    def list_busy(self, start, end):
        return [Busy(e["start"], e["end"], i, e["all_day"], e["title"])
                for i, e in self.events.items() if e["start"] < end and e["end"] > start]

    def create_event(self, *, start, end, summary, description, phone, service_key):
        return self.add_blocker(start, end, phone, service_key)

    def _appt(self, eid, e):
        return Appt(eid, e["start"], e["end"], e["service_key"], e["phone"])

    def get_event(self, event_id):
        e = self.events.get(event_id)
        return self._appt(event_id, e) if e and e["phone"] else None

    def move_event(self, event_id, start, end):
        self.events[event_id].update(start=start, end=end)

    def delete_event(self, event_id):
        del self.events[event_id]

    def append_note(self, event_id, text):
        self.events[event_id]["notes"].append(text)

    def find_by_phone(self, phone, from_dt):
        return [self._appt(i, e) for i, e in self.events.items() if e["phone"] == phone and e["start"] >= from_dt]


class FakeLedger:
    """Records what would have been written to the Sheet."""

    def __init__(self):
        self.contacts: list[dict] = []
        self.logs: list[dict] = []

    def upsert_contact(self, ctx, name=None, phone=None, vehicle=None):
        self.contacts.append(dict(name=name, phone=phone, vehicle=vehicle))

    def log(self, ctx, topic, summary, handoff=False):
        self.logs.append(dict(topic=topic, summary=summary, handoff=handoff, phone=ctx.phone))
