"""Google Sheets adapter. Two tabs:

  Contacts : ONE ROW PER CALLER (key = phone). Name, vehicle, first/last contact, number of calls,
             whether a human needs to call them back, and a running History of what came up.
  CallLog  : one row per thing that happened (also covers callers who never gave a number).
"""
import logging
import re
import threading
from datetime import datetime

from . import config
from .models import CallContext
from .phone import format_phone

log = logging.getLogger(__name__)

CONTACT_HEADERS = ["Phone", "Name", "Vehicle", "First Contact", "Last Contact", "Total Calls", "Needs Callback", "History"]
LOG_HEADERS = ["Timestamp", "Session", "Phone", "Name", "Topic", "Summary", "Handoff"]
TABS = {"Contacts": CONTACT_HEADERS, "CallLog": LOG_HEADERS}


class SheetsLedger:
    def __init__(self, service, sheet_id: str | None = None, tz=config.SHOP_TZ):
        self.api = service.spreadsheets()
        self.sheet_id = sheet_id or config.GOOGLE_SHEET_ID
        if not self.sheet_id:
            raise RuntimeError("GOOGLE_SHEET_ID is not set")
        self.tz = tz
        self._lock = threading.Lock()

    def _stamp(self) -> str:
        return datetime.now(self.tz).strftime("%Y-%m-%d %H:%M")

    def ensure_tabs(self) -> None:
        """Create the tabs / header rows if they're missing (safe to run repeatedly)."""
        meta = self.api.get(spreadsheetId=self.sheet_id, fields="sheets.properties.title").execute()
        have = {s["properties"]["title"] for s in meta["sheets"]}
        missing = [t for t in TABS if t not in have]
        if missing:
            self.api.batchUpdate(
                spreadsheetId=self.sheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": t}}} for t in missing]},
            ).execute()
        for tab, headers in TABS.items():
            first = self.api.values().get(spreadsheetId=self.sheet_id, range=f"{tab}!A1:A1").execute()
            if not first.get("values"):
                self.api.values().update(
                    spreadsheetId=self.sheet_id, range=f"{tab}!A1", valueInputOption="RAW", body={"values": [headers]}
                ).execute()

    # ----- Contacts -----
    def _find_row(self, digits: str) -> tuple[int | None, list[str]]:
        rows = self.api.values().get(spreadsheetId=self.sheet_id, range="Contacts!A:H").execute().get("values", [])
        for i, row in enumerate(rows[1:], start=2):  # row 1 is the header
            if row and re.sub(r"\D", "", row[0]) == digits:
                return i, row + [""] * (len(CONTACT_HEADERS) - len(row))
        return None, []

    def _touch_contact(self, ctx: CallContext, history_line: str | None = None, handoff: bool = False) -> None:
        if not ctx.phone:
            return  # nobody to key a row on yet; the CallLog still records it
        now = self._stamp()
        with self._lock:
            idx, row = self._find_row(ctx.phone)
            if idx is None:
                row = [format_phone(ctx.phone), "", "", now, now, "0", "", ""]
            row[1] = ctx.name or row[1]
            row[2] = ctx.vehicle or row[2]
            row[4] = now
            if not ctx.call_counted:
                row[5] = str(int(row[5] or 0) + 1)
                ctx.call_counted = True
            if handoff:
                row[6] = "YES"
            if history_line:
                row[7] = (row[7] + "\n" if row[7] else "") + f"[{now}] {history_line}"
            if idx is None:
                self.api.values().append(
                    spreadsheetId=self.sheet_id, range="Contacts!A:H", valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS", body={"values": [row]},
                ).execute()
            else:
                self.api.values().update(
                    spreadsheetId=self.sheet_id, range=f"Contacts!A{idx}:H{idx}", valueInputOption="RAW",
                    body={"values": [row]},
                ).execute()

    def upsert_contact(self, ctx: CallContext, name=None, phone=None, vehicle=None) -> None:
        self._touch_contact(ctx)

    def log(self, ctx: CallContext, topic: str, summary: str, handoff: bool = False) -> None:
        with self._lock:
            self.api.values().append(
                spreadsheetId=self.sheet_id, range="CallLog!A:G", valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [[self._stamp(), ctx.session_id, format_phone(ctx.phone) if ctx.phone else "",
                                  ctx.name or "", topic, summary, "YES" if handoff else ""]]},
            ).execute()
        self._touch_contact(ctx, history_line=f"{topic}: {summary}", handoff=handoff)
