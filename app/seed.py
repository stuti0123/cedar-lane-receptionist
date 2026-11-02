"""Put a few appointments on the calendar so conflicts/reschedules can be tested, and create the Sheet tabs.

    python -m app.seed

Re-running is safe: it deletes the events it created before (tagged seed=1) and recreates them,
always relative to today, so "next Thursday 3:30" is always in the future."""
from datetime import date, datetime, time, timedelta

from . import config, shop
from .gcal import GoogleCalendar, google_service
from .sheets import SheetsLedger

TZ = config.SHOP_TZ


def next_weekday(target: int, after: date) -> date:
    """First date strictly after `after` that falls on weekday `target` (Mon=0)."""
    return after + timedelta(days=(target - after.weekday() - 1) % 7 + 1)


def main() -> None:
    cal = GoogleCalendar()
    svc = cal.service.events()

    # remove previous seed events
    now = datetime.now(TZ)
    for e in cal._events(timeMin=now.isoformat(), privateExtendedProperty="seed=1"):
        svc.delete(calendarId=cal.calendar_id, eventId=e["id"]).execute()

    today = datetime.now(TZ).date()
    thu, tue = next_weekday(3, today), next_weekday(1, today)
    fri = thu + timedelta(days=1)

    def add(day: date, start: time, service_key: str, name: str, phone: str) -> None:
        s = shop.SERVICES[service_key]
        begin = datetime.combine(day, start, tzinfo=TZ)
        cal.create_event(
            start=begin, end=begin + timedelta(minutes=s.minutes), summary=f"{s.name} - {name}",
            description=f"Phone: {phone}\nSeeded test appointment", phone="".join(c for c in phone if c.isdigit()),
            service_key=service_key,
        )

    # tag seeded events so a re-run can find them
    original_create = cal.create_event

    def tagged_create(**kw):
        eid = original_create(**kw)
        svc.patch(calendarId=cal.calendar_id, eventId=eid,
                  body={"extendedProperties": {"private": {"seed": "1", "phone": kw["phone"],
                                                           "service": kw["service_key"], "source": "seed"}}}).execute()
        return eid

    cal.create_event = tagged_create  # type: ignore[method-assign]

    add(thu, time(15, 30), "interior_detail_wash", "Jordan Lee", "415-555-0111")  # the assignment's "3:30 is full"
    add(thu, time(10, 0), "full_detail", "Maria Gomez", "415-555-0122")
    add(tue, time(9, 0), "exterior_wash_wax", "Sam Patel", "415-555-0133")
    add(tue, time(14, 0), "full_detail", "Ana Ruiz", "415-555-0144")

    # all-day event with no phone: the whole day is blocked (tests the "closed day" path)
    svc.insert(calendarId=cal.calendar_id, body={
        "summary": "Shop closed - staff training", "start": {"date": fri.isoformat()},
        "end": {"date": (fri + timedelta(days=1)).isoformat()},
        "extendedProperties": {"private": {"seed": "1"}},
    }).execute()

    SheetsLedger(google_service("sheets", "v4")).ensure_tabs()
    print(f"Seeded. Thursday {thu}: 10:00 full detail, 3:30 PM interior detail & wash (taken). "
          f"Tuesday {tue}: 9:00 and 2:00 PM. Friday {fri}: closed all day.")
    print("Test phones with appointments: 415-555-0111, 0122, 0133, 0144")


if __name__ == "__main__":
    main()
