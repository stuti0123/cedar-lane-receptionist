"""The tools the LLM can call. Each one is a thin wrapper: parse args -> call Booking -> log to the
Sheet. All business rules live in booking.py; the LLM only decides WHICH tool to call and what to say."""
import logging
from dataclasses import dataclass
from typing import Protocol

from . import shop
from .booking import Booking, BookingError
from .models import CallContext
from .phone import format_phone, normalize_phone

log = logging.getLogger(__name__)


class Ledger(Protocol):
    def upsert_contact(self, ctx: CallContext, name=None, phone=None, vehicle=None) -> None: ...
    def log(self, ctx: CallContext, topic: str, summary: str, handoff: bool = False) -> None: ...


@dataclass
class Deps:
    booking: Booking
    ledger: Ledger


SERVICE_KEYS = list(shop.SERVICES)
DATE = {"type": "string", "description": "YYYY-MM-DD, shop local date."}
TIME24 = {"type": "string", "description": "24-hour HH:MM, e.g. 15:30."}
PHONE = {"type": "string", "description": "Caller's 10-digit US phone number."}

TOOL_SPECS = [
    {
        "name": "check_availability",
        "description": "Check open appointment times for a service on a date. ALWAYS call this before saying whether a time is "
                       "free. Pass preferred_time if the caller asked for a specific time.",
        "input_schema": {"type": "object", "properties": {
            "date": DATE, "service": {"type": "string", "enum": SERVICE_KEYS}, "preferred_time": TIME24},
            "required": ["date", "service"]},
    },
    {
        "name": "book_appointment",
        "description": "Book an appointment. Only call once you have service, date, time, the caller's name AND phone number, "
                       "and the caller has agreed to the slot.",
        "input_schema": {"type": "object", "properties": {
            "date": DATE, "time": TIME24, "service": {"type": "string", "enum": SERVICE_KEYS},
            "name": {"type": "string"}, "phone": PHONE,
            "vehicle": {"type": "string", "description": "e.g. '2019 Honda Civic' or 'SUV'. Optional."},
            "notes": {"type": "string"}},
            "required": ["date", "time", "service", "name", "phone"]},
    },
    {
        "name": "find_appointments",
        "description": "List a caller's upcoming appointments by phone number. Use before rescheduling, cancelling, or "
                       "handling a 'running late' call.",
        "input_schema": {"type": "object", "properties": {"phone": PHONE}, "required": ["phone"]},
    },
    {
        "name": "reschedule_appointment",
        "description": "Move an existing appointment to a new date/time (same service). Call check_availability first.",
        "input_schema": {"type": "object", "properties": {
            "event_id": {"type": "string"}, "phone": PHONE, "new_date": DATE, "new_time": TIME24},
            "required": ["event_id", "phone", "new_date", "new_time"]},
    },
    {
        "name": "cancel_appointment",
        "description": "Cancel an appointment. Confirm with the caller first.",
        "input_schema": {"type": "object", "properties": {"event_id": {"type": "string"}, "phone": PHONE},
                         "required": ["event_id", "phone"]},
    },
    {
        "name": "report_running_late",
        "description": "Record that the caller is running late for TODAY's appointment.",
        "input_schema": {"type": "object", "properties": {
            "event_id": {"type": "string"}, "phone": PHONE, "minutes_late": {"type": "integer"}},
            "required": ["event_id", "phone", "minutes_late"]},
    },
    {
        "name": "log_call_note",
        "description": "Log an informational call (pricing, hours, prep, vehicle question...) after answering it. "
                       "Include name/phone only if the caller already gave them; do not ask just for this.",
        "input_schema": {"type": "object", "properties": {
            "topic": {"type": "string", "enum": ["pricing", "hours", "prep_requirements", "vehicle_question", "other"]},
            "summary": {"type": "string", "description": "One short sentence of what the caller asked and what you said."},
            "name": {"type": "string"}, "phone": PHONE},
            "required": ["topic", "summary"]},
    },
    {
        "name": "handoff_to_human",
        "description": "Hand the caller to a human. Required for: " + "; ".join(shop.HANDOFF_TOPICS)
                       + ". Get their name and callback number first. Do not promise outcomes or timelines.",
        "input_schema": {"type": "object", "properties": {
            "category": {"type": "string", "enum": ["charge_complaint", "custom_quote", "unsupported_vehicle",
                                                    "damage_claim", "angry_caller", "other"]},
            "summary": {"type": "string", "description": "What happened, in enough detail for staff to call back informed."},
            "name": {"type": "string"}, "callback_phone": PHONE},
            "required": ["category", "summary", "name", "callback_phone"]},
    },
]


def _remember(ctx: CallContext, name=None, phone=None, vehicle=None) -> None:
    """Store what we've learned about the caller on the conversation context."""
    if phone:
        try:
            ctx.phone = normalize_phone(phone)
        except ValueError:
            pass  # invalid numbers are reported by the tool itself
    ctx.name = name or ctx.name
    ctx.vehicle = vehicle or ctx.vehicle


def _write_sheet(fn, *args, **kwargs) -> None:
    """A Sheets outage must never undo or hide a booking that already succeeded."""
    try:
        fn(*args, **kwargs)
    except Exception:
        log.exception("Sheet write failed")


def run_tool(name: str, args: dict, ctx: CallContext, deps: Deps) -> dict:
    b, ledger = deps.booking, deps.ledger
    try:
        if name == "check_availability":
            return {"ok": True, **b.check(args["date"], args["service"], args.get("preferred_time"))}

        if name == "book_appointment":
            if ctx.confirm_booking:
                key = (args["date"], args["time"], args["service"], "".join(c for c in str(args["phone"]) if c.isdigit()))
                if ctx.pending_booking != key or ctx.pending_turn >= ctx.turn:  # not yet confirmed by the caller
                    ctx.pending_booking, ctx.pending_turn = key, ctx.turn
                    return {"ok": False, "error": "confirm_first",
                            "message": "STOP - NOT booked yet. Do not call any tool now. Reply to the caller: read "
                                       "the service, day, time and the phone number digit by digit back and ask them to "
                                       "confirm. Only after the caller says yes, call book_appointment again with exactly "
                                       "the same details (use corrected details if they change anything)."}
            r = b.book(args["date"], args["time"], args["service"], args["name"], args["phone"],
                       args.get("vehicle"), args.get("notes"))
            _remember(ctx, args["name"], args["phone"], args.get("vehicle"))
            _write_sheet(ledger.log, ctx, "booking", f"Booked {r['service']} {r['weekday']} {r['date']} {r['start']}")
            return {"ok": True, **r}

        if name == "find_appointments":
            appts = b.find(args["phone"])
            _remember(ctx, phone=args["phone"])
            return {"ok": True, "appointments": appts}

        if name == "reschedule_appointment":
            r = b.reschedule(args["event_id"], args["phone"], args["new_date"], args["new_time"])
            _remember(ctx, phone=args["phone"])
            _write_sheet(ledger.log, ctx, "reschedule", f"{r['service']}: {r['old']} -> {r['new']}")
            return {"ok": True, **r}

        if name == "cancel_appointment":
            r = b.cancel(args["event_id"], args["phone"])
            _remember(ctx, phone=args["phone"])
            _write_sheet(ledger.log, ctx, "cancellation", f"Cancelled {r['service']} ({r['was']})")
            return {"ok": True, **r}

        if name == "report_running_late":
            r = b.mark_late(args["event_id"], args["phone"], int(args["minutes_late"]))
            _remember(ctx, phone=args["phone"])
            _write_sheet(ledger.log, ctx, "running_late",
                         f"~{r['minutes_late']} min late for {r['appointment']} (within grace: {r['within_grace']})")
            return {"ok": True, **r}

        if name == "log_call_note":
            _remember(ctx, args.get("name"), args.get("phone"))
            _write_sheet(ledger.log, ctx, args["topic"], args["summary"])
            return {"ok": True}

        if name == "handoff_to_human":
            phone = normalize_phone(args["callback_phone"])  # a handoff without a working number is useless
            _remember(ctx, args["name"], phone)
            _write_sheet(ledger.log, ctx, f"handoff:{args['category']}", args["summary"], handoff=True)
            return {"ok": True, "message": "Recorded. A team member will call them back at " + format_phone(phone)}

        return {"ok": False, "error": "unknown_tool", "message": f"No tool named {name}"}

    except BookingError as e:
        return {"ok": False, "error": e.code, "message": e.message, **e.extra}
    except ValueError as e:  # bad phone in handoff
        return {"ok": False, "error": "bad_phone", "message": str(e)}
    except KeyError as e:
        return {"ok": False, "error": "bad_arguments", "message": f"Missing argument {e}"}
