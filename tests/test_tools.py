"""Tool layer: argument handling, context tracking and Sheet logging (with fakes)."""
import pytest

from app.models import CallContext
from app.tools import Deps, run_tool
from fakes import FakeLedger

PHONE = "415-555-0190"


@pytest.fixture
def env(booking):
    ledger = FakeLedger()
    return Deps(booking, ledger), CallContext("s1"), ledger


def test_booking_logs_and_remembers_caller(env):
    deps, ctx, ledger = env
    out = run_tool("book_appointment", dict(date="2026-11-05", time="16:30", service="interior_detail_wash",
                                            name="Sara", phone=PHONE), ctx, deps)
    assert out["ok"] and ctx.phone == "4155550190" and ctx.name == "Sara"
    assert ledger.logs[0]["topic"] == "booking" and "4:30 PM" in ledger.logs[0]["summary"]


def test_failed_booking_is_not_logged_as_booked(env, cal):
    from conftest import at
    deps, ctx, ledger = env
    cal.add_blocker(at(5, 15, 30), at(5, 16, 30))
    out = run_tool("book_appointment", dict(date="2026-11-05", time="15:30", service="interior_detail_wash",
                                            name="Sara", phone=PHONE), ctx, deps)
    assert out["ok"] is False and out["error"] == "taken" and out["same_day"]
    assert ledger.logs == []


def test_handoff_needs_valid_callback_number(env):
    deps, ctx, ledger = env
    bad = run_tool("handoff_to_human", dict(category="charge_complaint", summary="x", name="Bob",
                                            callback_phone="12"), ctx, deps)
    assert bad["ok"] is False and bad["error"] == "bad_phone" and ledger.logs == []
    good = run_tool("handoff_to_human", dict(category="charge_complaint", summary="Charged twice", name="Bob",
                                             callback_phone="(415) 555-0100"), ctx, deps)
    assert good["ok"] and ledger.logs[0]["handoff"] is True


def test_missing_argument_and_unknown_tool(env):
    deps, ctx, _ = env
    assert run_tool("book_appointment", {"date": "2026-11-05"}, ctx, deps)["error"] == "bad_arguments"
    assert run_tool("make_coffee", {}, ctx, deps)["error"] == "unknown_tool"


def test_sheet_failure_does_not_fail_the_booking(env):
    deps, ctx, ledger = env

    def boom(*a, **k):
        raise RuntimeError("sheets down")

    ledger.log = boom
    out = run_tool("book_appointment", dict(date="2026-11-05", time="10:00", service="express_wash",
                                            name="Sara", phone=PHONE), ctx, deps)
    assert out["ok"] is True


def test_voice_booking_needs_a_readback_confirmation_first(env, cal):
    deps, ctx, ledger = env
    ctx.confirm_booking = True
    args = dict(date="2026-11-05", time="16:30", service="interior_detail_wash", name="Sara", phone="452 555 0123")
    ctx.turn = 1
    first = run_tool("book_appointment", args, ctx, deps)
    assert first["ok"] is False and first["error"] == "confirm_first" and cal.events == {}
    assert run_tool("book_appointment", args, ctx, deps)["error"] == "confirm_first"  # same caller message: still no
    ctx.turn = 2  # the caller answered
    assert run_tool("book_appointment", dict(args, phone="415 555 0123"), ctx, deps)["error"] == "confirm_first"  # corrected number: ask again
    ctx.turn = 3
    assert run_tool("book_appointment", dict(args, phone="415 555 0123"), ctx, deps)["ok"] is True
    assert len(cal.events) == 1


def test_text_chat_books_without_the_voice_confirmation(env):
    deps, ctx, _ = env  # confirm_booking is off by default
    assert run_tool("book_appointment", dict(date="2026-11-05", time="10:00", service="express_wash",
                                             name="Sara", phone=PHONE), ctx, deps)["ok"] is True


def test_voice_booking_reads_the_number_back_in_groups_and_refuses_partial_numbers(env, cal):
    deps, ctx, _ = env
    ctx.confirm_booking, ctx.turn = True, 1
    args = dict(date="2026-11-05", time="16:30", service="interior_detail_wash", name="Sara")
    partial = run_tool("book_appointment", dict(args, phone="415 550 190"), ctx, deps)  # only 9 digits
    assert partial["error"] == "bad_phone" and cal.events == {}
    ok = run_tool("book_appointment", dict(args, phone="415-555-0190"), ctx, deps)
    assert ok["error"] == "confirm_first" and ok["read_back_phone"] == "four one five, five five five, zero one nine zero"
    assert ok["read_back_phone"] in ok["message"]


def test_voice_booking_rejects_a_number_the_caller_never_said(env, cal):
    deps, ctx, _ = env
    ctx.confirm_booking, ctx.turn = True, 5
    args = dict(date="2026-11-05", time="14:30", service="interior_detail_wash", name="Sara")
    ctx.recent_user_texts = ["Yes. The number is 415. 5. 55. 0190."]
    bad = run_tool("book_appointment", dict(args, phone="4155500190"), ctx, deps)  # a digit went missing
    assert bad["error"] == "phone_mismatch" and bad["heard_phone"] == "4155550190" and cal.events == {}
    assert run_tool("book_appointment", dict(args, phone="4155550190"), ctx, deps)["error"] == "confirm_first"


def test_number_check_is_skipped_when_the_words_hold_no_digits(env):
    deps, ctx, _ = env
    ctx.confirm_booking, ctx.turn = True, 5
    ctx.recent_user_texts = ["four one five five five five oh one ninety"]
    out = run_tool("book_appointment", dict(date="2026-11-05", time="14:30", service="interior_detail_wash",
                                            name="Sara", phone="4155550190"), ctx, deps)
    assert out["error"] == "confirm_first"  # the read-back still protects us


def test_fragmented_number_without_a_full_match_asks_to_repeat(env):
    deps, ctx, _ = env
    ctx.confirm_booking, ctx.turn = True, 5
    ctx.recent_user_texts = ["415. 550.", "415. 550. 190."]  # only 9 digits were heard
    out = run_tool("book_appointment", dict(date="2026-11-05", time="14:30", service="interior_detail_wash",
                                            name="Sara", phone="4155500190"), ctx, deps)
    assert out["error"] == "phone_mismatch" and "heard_phone" not in out and "repeat" in out["message"] or "again" in out["message"]
