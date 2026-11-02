"""Builds the real agent: Google Calendar + Google Sheets + Claude. The web server and the
terminal chat both start from here, so there is exactly one place that wires things together."""
from . import config
from .agent import Agent
from .booking import Booking
from .gcal import GoogleCalendar, google_service
from .sheets import SheetsLedger
from .tools import Deps


def build_agent() -> Agent:
    config.validate()
    ledger = SheetsLedger(google_service("sheets", "v4"))
    ledger.ensure_tabs()  # creates the Contacts / CallLog tabs if they don't exist yet
    return Agent(Deps(booking=Booking(GoogleCalendar()), ledger=ledger))
