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
