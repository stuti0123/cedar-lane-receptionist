import pytest

from app.booking import BookingError
from conftest import at

PHONE = "415-555-0190"


def seed_thursday_330(cal):
    """The shop's existing 3:30-4:30 appointment from the assignment example."""
    cal.add_blocker(at(5, 15, 30), at(5, 16, 30), phone="4155550111", service_key="interior_detail_wash")


def labels(slots):
    return [s["label"] for s in slots]


def test_check_reports_taken_slot_and_offers_430(booking, cal):
    seed_thursday_330(cal)
    r = booking.check("2026-11-05", "interior_detail_wash", preferred="15:30")
    assert r["preferred_available"] is False
    assert "4:30 PM" in labels(r["nearest_alternatives"])
    assert "3:30 PM" not in labels(r["open_slots"])


def test_check_closed_day_points_to_next_open_day(booking):
    r = booking.check("2026-11-08", "full_detail")  # Sunday
    assert r["closed"] and r["next_open_day"]["weekday"] == "Monday"


def test_book_happy_path_and_double_booking_rejected(booking, cal):
    seed_thursday_330(cal)
    r = booking.book("2026-11-05", "16:30", "interior_detail_wash", "Sara", PHONE, vehicle="Civic")
    assert r["start"] == "4:30 PM" and r["end"] == "5:30 PM" and r["phone"] == PHONE
    with pytest.raises(BookingError) as e:
        booking.book("2026-11-05", "16:30", "interior_detail_wash", "Someone", "4155550999")
    assert e.value.code == "taken" and e.value.extra["same_day"]


def test_book_taken_slot_returns_alternatives(booking, cal):
    seed_thursday_330(cal)
    with pytest.raises(BookingError) as e:
        booking.book("2026-11-05", "15:30", "interior_detail_wash", "Sara", PHONE)
    assert e.value.code == "taken"
    assert "4:30 PM" in labels(e.value.extra["same_day"])


def test_bad_inputs(booking):
    with pytest.raises(BookingError) as e:
        booking.book("2026-11-05", "16:30", "interior_detail_wash", "Sara", "123")
    assert e.value.code == "bad_phone"
    with pytest.raises(BookingError) as e:
        booking.book("2026-11-05", "16:30", "interior_detail_wash", "  ", PHONE)
    assert e.value.code == "missing_name"
    with pytest.raises(BookingError) as e:
        booking.book("2026-11-05", "16:30", "ceramic", "Sara", PHONE)
    assert e.value.code == "unknown_service"
    with pytest.raises(BookingError) as e:
        booking.check("2026-11-01", "full_detail")
    assert e.value.code == "past_date"


def test_duplicate_booking_same_caller_same_time(booking, cal):
    booking.book("2026-11-05", "10:00", "express_wash", "Sara", PHONE)
    cal.events.clear()  # simulate a retry after the first write: slot looks free but caller already has it
    cal.add_blocker(at(5, 10), at(5, 10, 30), phone="4155550190", service_key="express_wash")
    # the slot is occupied by her own appointment, so the retry is refused either way
    with pytest.raises(BookingError):
        booking.book("2026-11-05", "10:00", "express_wash", "Sara", PHONE)


def test_reschedule_into_overlap_with_self_is_allowed(booking):
    r = booking.book("2026-11-05", "10:00", "interior_detail_wash", "Sara", PHONE)
    out = booking.reschedule(r["event_id"], PHONE, "2026-11-05", "10:30")
    assert "10:30 AM" in out["new"]


def test_reschedule_to_taken_slot_fails_and_wrong_phone_hidden(booking, cal):
    seed_thursday_330(cal)
    r = booking.book("2026-11-05", "10:00", "interior_detail_wash", "Sara", PHONE)
    with pytest.raises(BookingError) as e:
        booking.reschedule(r["event_id"], PHONE, "2026-11-05", "15:30")
    assert e.value.code == "taken"
    with pytest.raises(BookingError) as e:
        booking.reschedule(r["event_id"], "4155550000", "2026-11-05", "11:00")
    assert e.value.code == "not_found"


def test_cannot_touch_an_event_without_a_phone(booking, cal):
    eid = cal.add_blocker(at(5, 9), at(5, 10))
    with pytest.raises(BookingError) as e:
        booking.cancel(eid, PHONE)
    assert e.value.code == "not_found"


def test_cancel_reopens_slot(booking):
    r = booking.book("2026-11-05", "10:00", "interior_detail_wash", "Sara", PHONE)
    booking.cancel(r["event_id"], PHONE)
    assert booking.find(PHONE) == []
    assert booking.check("2026-11-05", "interior_detail_wash", "10:00")["preferred_available"] is True


def test_find_returns_upcoming_sorted(booking):
    booking.book("2026-11-06", "10:00", "express_wash", "Sara", PHONE)
    booking.book("2026-11-05", "10:00", "express_wash", "Sara", PHONE)
    assert [a["date"] for a in booking.find(PHONE)] == ["2026-11-05", "2026-11-06"]


def test_running_late(booking, cal):
    r = booking.book("2026-11-02", "14:00", "express_wash", "Sara", PHONE)  # today
    ok = booking.mark_late(r["event_id"], PHONE, 10)
    assert ok["within_grace"] is True
    assert booking.mark_late(r["event_id"], PHONE, 30)["within_grace"] is False
    assert cal.events[r["event_id"]]["notes"]
    other = booking.book("2026-11-05", "10:00", "express_wash", "Sara", PHONE)
    with pytest.raises(BookingError) as e:
        booking.mark_late(other["event_id"], PHONE, 10)
    assert e.value.code == "not_today"
