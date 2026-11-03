"""Google adapter: an all-day event must come back flagged all_day with its title (no network: fake service)."""
from types import SimpleNamespace as NS

from app.config import SHOP_TZ
from app.gcal import GoogleCalendar
from conftest import at


class FakeEvents:
    def __init__(self, items):
        self.items = items

    def list(self, **kw):
        return NS(execute=lambda num_retries=0: {"items": self.items})


def calendar_with(items):
    return GoogleCalendar(service=NS(events=lambda: FakeEvents(items)), calendar_id="cal", tz=SHOP_TZ)


def test_all_day_and_timed_events_are_told_apart():
    cal = calendar_with([
        {"id": "a", "summary": "Shop closed - staff training", "start": {"date": "2026-11-06"}, "end": {"date": "2026-11-07"}},
        {"id": "b", "summary": "Full Detail - Sam", "start": {"dateTime": "2026-11-05T10:00:00-08:00"},
         "end": {"dateTime": "2026-11-05T12:00:00-08:00"}},
        {"id": "c", "summary": "free note", "transparency": "transparent", "start": {"date": "2026-11-05"},
         "end": {"date": "2026-11-06"}},
    ])
    busy = {b.event_id: b for b in cal.list_busy(at(5, 0), at(8, 0))}
    assert set(busy) == {"a", "b"}  # "c" is marked free, so it is ignored
    assert busy["a"].all_day and busy["a"].title == "Shop closed - staff training"
    assert busy["a"].start == at(6, 0) and busy["a"].end == at(7, 0)
    assert busy["b"].all_day is False
