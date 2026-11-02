"""Google Calendar adapter (implements booking.CalendarPort). Auth = service account:
create a dedicated calendar and share it with the service account email ("Make changes to events")."""
from datetime import datetime, time, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import config
from .models import Appt, Busy

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
]


def google_service(name: str, version: str):
    creds = service_account.Credentials.from_service_account_file(config.GOOGLE_CREDENTIALS_FILE, scopes=SCOPES)
    return build(name, version, credentials=creds, cache_discovery=False)


class GoogleCalendar:
    def __init__(self, service=None, calendar_id: str | None = None, tz=config.SHOP_TZ):
        self.service = service or google_service("calendar", "v3")
        self.calendar_id = calendar_id or config.GOOGLE_CALENDAR_ID
        if not self.calendar_id:
            raise RuntimeError("GOOGLE_CALENDAR_ID is not set")
        self.tz = tz

    # ----- helpers -----
    def _events(self, **params) -> list[dict]:
        items, token = [], None
        while True:
            resp = self.service.events().list(
                calendarId=self.calendar_id, singleEvents=True, orderBy="startTime",
                maxResults=250, pageToken=token, **params,
            ).execute()
            items += resp.get("items", [])
            token = resp.get("nextPageToken")
            if not token:
                return items

    def _times(self, e: dict) -> tuple[datetime, datetime]:
        s, en = e["start"], e["end"]
        if "dateTime" in s:
            return (datetime.fromisoformat(s["dateTime"]).astimezone(self.tz),
                    datetime.fromisoformat(en["dateTime"]).astimezone(self.tz))
        # all-day event: blocks the whole day(s). Google's end date is exclusive.
        day = lambda d: datetime.combine(datetime.fromisoformat(d).date(), time(0), tzinfo=self.tz)  # noqa: E731
        return day(s["date"]), day(en["date"])

    def _to_appt(self, e: dict) -> Appt | None:
        props = e.get("extendedProperties", {}).get("private", {})
        if e.get("status") == "cancelled" or "phone" not in props or "service" not in props:
            return None
        start, end = self._times(e)
        return Appt(e["id"], start, end, props["service"], props["phone"], e.get("summary", ""))

    def _dt(self, d: datetime) -> dict:
        return {"dateTime": d.isoformat(), "timeZone": self.tz.key}

    # ----- CalendarPort -----
    def list_busy(self, start, end):
        out = []
        for e in self._events(timeMin=start.isoformat(), timeMax=end.isoformat()):
            if e.get("transparency") == "transparent":  # marked "free" by whoever made it
                continue
            s, en = self._times(e)
            out.append(Busy(s, en, e["id"]))
        return out

    def create_event(self, *, start, end, summary, description, phone, service_key):
        body = {
            "summary": summary, "description": description,
            "start": self._dt(start), "end": self._dt(end),
            "extendedProperties": {"private": {"phone": phone, "service": service_key, "source": "ai-receptionist"}},
        }
        return self.service.events().insert(calendarId=self.calendar_id, body=body).execute()["id"]

    def get_event(self, event_id):
        try:
            e = self.service.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
        except HttpError as err:
            if err.resp.status in (404, 410):
                return None
            raise
        return self._to_appt(e)

    def move_event(self, event_id, start, end):
        self.service.events().patch(
            calendarId=self.calendar_id, eventId=event_id, body={"start": self._dt(start), "end": self._dt(end)}
        ).execute()

    def delete_event(self, event_id):
        self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()

    def append_note(self, event_id, text):
        e = self.service.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
        desc = (e.get("description") or "") + "\n" + text
        self.service.events().patch(calendarId=self.calendar_id, eventId=event_id, body={"description": desc}).execute()

    def find_by_phone(self, phone, from_dt):
        events = self._events(timeMin=from_dt.isoformat(), privateExtendedProperty=f"phone={phone}")
        return [a for a in map(self._to_appt, events) if a]
