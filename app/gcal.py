"""Google Calendar adapter (implements booking.CalendarPort).

Two ways to sign in, chosen automatically by what GOOGLE_APPLICATION_CREDENTIALS points to:
  * a service-account key file  -> share the calendar/sheet with the service account's email
  * an OAuth "Desktop app" file -> run `python -m app.login` once; it works on YOUR OWN calendar and sheet
    (use this when Google Cloud blocks service-account key creation)."""
import json
from datetime import datetime, time, timedelta
from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import config
from .models import Appt, Busy

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
]


def credentials_kind(path: str) -> str:
    """'service_account' for a service-account key file, 'oauth' for an OAuth Desktop-app client file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("type") == "service_account":
        return "service_account"
    if "installed" in data:
        return "oauth"
    raise RuntimeError(f"{path} is neither a service-account key nor an OAuth 'Desktop app' client file")


def login() -> None:
    """One-time browser sign-in. Saves token.json so the server can later run without a browser."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(config.GOOGLE_CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    Path(config.GOOGLE_TOKEN_FILE).write_text(creds.to_json(), encoding="utf-8")


def _oauth_credentials() -> Credentials:
    token = Path(config.GOOGLE_TOKEN_FILE)
    if not token.is_file():
        raise RuntimeError("Not signed in to Google yet. Run:  python -m app.login")
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds.valid:  # the short-lived access token ran out: swap the saved refresh token for a new one
        try:
            creds.refresh(Request())
        except GoogleAuthError:
            raise RuntimeError("Your Google sign-in has expired. Run again:  python -m app.login")
        token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def google_service(name: str, version: str):
    path = config.GOOGLE_CREDENTIALS_FILE
    if credentials_kind(path) == "service_account":
        creds = service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    else:
        creds = _oauth_credentials()
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
            ).execute(num_retries=3)
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
            out.append(Busy(s, en, e["id"], all_day="date" in e["start"], title=e.get("summary", "")))
        return out

    def create_event(self, *, start, end, summary, description, phone, service_key):
        body = {
            "summary": summary, "description": description,
            "start": self._dt(start), "end": self._dt(end),
            "extendedProperties": {"private": {"phone": phone, "service": service_key, "source": "ai-receptionist"}},
        }
        return self.service.events().insert(calendarId=self.calendar_id, body=body).execute(num_retries=3)["id"]

    def get_event(self, event_id):
        try:
            e = self.service.events().get(calendarId=self.calendar_id, eventId=event_id).execute(num_retries=3)
        except HttpError as err:
            if err.resp.status in (404, 410):
                return None
            raise
        return self._to_appt(e)

    def move_event(self, event_id, start, end):
        self.service.events().patch(
            calendarId=self.calendar_id, eventId=event_id, body={"start": self._dt(start), "end": self._dt(end)}
        ).execute(num_retries=3)

    def delete_event(self, event_id):
        self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute(num_retries=3)

    def append_note(self, event_id, text):
        e = self.service.events().get(calendarId=self.calendar_id, eventId=event_id).execute(num_retries=3)
        desc = (e.get("description") or "") + "\n" + text
        self.service.events().patch(calendarId=self.calendar_id, eventId=event_id, body={"description": desc}).execute(num_retries=3)

    def find_by_phone(self, phone, from_dt):
        events = self._events(timeMin=from_dt.isoformat(), privateExtendedProperty=f"phone={phone}")
        return [a for a in map(self._to_appt, events) if a]
