# Cedar Lane Auto Detailing: AI receptionist (Phase 1: text chat)

An LLM agent (Claude, tool use) that books, moves and cancels detailing appointments on **Google Calendar**,
keeps one row per caller in **Google Sheets**, answers shop questions, and hands off what it shouldn't handle.

New to the project? Read **[docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)** first (plain English, no jargon).

## Architecture

```
browser chat / terminal -> agent.py (LLM loop) -> tools.py (thin wrappers) -> booking.py (ALL the rules)
                                                                                |-> availability.py (pure logic)
                                                                                |-> gcal.py   (Google Calendar)
                                                       tools.py --------------> sheets.py (Google Sheets)
```

| File | Job |
|---|---|
| `app/shop.py` | Services, durations, prices, hours, prep, vehicle policy. One source of truth for both the scheduling logic and the prompt. **Prices/policies are placeholders.** |
| `app/availability.py` | Pure functions: is this slot bookable? (hours, 30-min grid, 1h notice, bay concurrency). No I/O. |
| `app/booking.py` | Check / book / find / reschedule / cancel / late. Re-checks availability right before writing, serializes check+insert with a lock, verifies the caller owns an appointment (phone match) before changing it. |
| `app/tools.py` | Tool schemas the LLM sees + dispatcher. Logs to the Sheet after each successful action. |
| `app/agent.py` | System prompt + tool-use loop (max 8 tool steps per message). |
| `app/gcal.py`, `app/sheets.py` | Google adapters (service account, automatic retries on transient errors). |
| `app/wiring.py` | Builds the real agent (validates config, creates Sheet tabs). |
| `app/server.py`, `static/index.html` | FastAPI server, `/chat`, `/health`, bare chat page. Conversations expire after 1h idle (`app/sessions.py`). |
| `app/cli.py` | Chat in the terminal and see the agent's tool calls. |
| `app/seed.py` | Puts test events on the calendar, creates the Sheet tabs. |

### Key design decisions

1. **The LLM never decides availability.** It must call `check_availability`; code computes it. The prompt forbids claiming a booking without an `ok` tool result.
2. **Business rules live in code, not the prompt.** Hours, durations, notice, conflicts and "can't touch someone else's booking" are enforced in `booking.py`, so a clever caller can't talk the model out of them.
3. **Conflicts count concurrency, not events** (`peak_overlap`): with 2 bays, two events that overlap my slot but not each other still leave a bay free. `BAYS = 1` by default, matching the assignment's example.
4. **Any event on the calendar blocks time**, including all-day ones (whole day blocked) and events not made by the agent. Events marked "free" are ignored.
5. **Race safety**: availability is re-checked inside a lock immediately before the insert.
6. **Identity = phone number**, stored on the event (`extendedProperties.private.phone`) so look-ups are a server-side filter, and as the Contacts key. Reschedule/cancel/late require the matching phone; "not found" and "not yours" return the same error.
7. **Sheet failures never undo a booking** (logged server-side instead).
8. **Handoff** = `handoff_to_human`: needs a valid callback number, sets `Needs Callback = YES` and writes a flagged CallLog row. No live transfer exists in a text chat.

### Sheet layout
- **Contacts**: Phone, Name, Vehicle, First Contact, Last Contact, Total Calls, Needs Callback, History (running log, newest last).
- **CallLog**: Timestamp, Session, Phone, Name, Topic, Summary, Handoff (one row per event; covers callers who never gave a number).

### What it handles vs hands off
Handles: book, reschedule, cancel, running late (15-min grace), pricing, hours, prep requirements, "do you detail X?".
Hands off: charge complaints/refunds, custom quotes (ceramic/paint correction), unsupported or unsure vehicles, damage claims, angry callers, anything not in `shop.py`.

## Setup

1. **Google Cloud**: create a project, enable the *Google Calendar API* and *Google Sheets API*.
2. **Service account**: IAM & Admin -> Service accounts -> Create. Open it -> Keys -> Add key -> JSON. Save the file as `service_account.json` in the project root (it is gitignored; never commit it). Copy the service account's email.
3. **Calendar**: in Google Calendar create a new calendar "Cedar Lane Bookings". Settings for that calendar -> *Share with specific people* -> add the service account email with **Make changes to events**. Under *Integrate calendar* copy the **Calendar ID**.
4. **Sheet**: create a new spreadsheet, Share -> add the service account email as **Editor**. Copy the ID from the URL (`/d/<THIS PART>/edit`).
5. Copy `.env.example` to `.env` and fill in `ANTHROPIC_API_KEY`, `GOOGLE_CALENDAR_ID`, `GOOGLE_SHEET_ID`.
6. Install and run (Git Bash on Windows shown; on Mac/Linux use `source .venv/bin/activate`):
   ```bash
   python -m venv .venv
   source .venv/Scripts/activate
   pip install -r requirements.txt
   python -m pytest              # 36 offline tests, no accounts needed
   python -m app.seed            # test events on the calendar + Sheet tabs
   python -m app.cli             # chat in the terminal, OR:
   python -m uvicorn app.server:app --reload    # then open http://localhost:8000
   ```

## Scenarios to try (`app.seed` prints the real dates)
1. **Assignment flow**: "Is 3:30 on Thursday open for an interior detail and wash?" -> taken, offers 4:30 -> "sure" -> name/phone -> booked. Check Calendar + Sheet.
2. **Reschedule**: phone 415-555-0122 (Maria): move her Thursday full detail to Tuesday 11am.
3. **Conflict on reschedule**: move to a taken time -> refuses and offers alternatives.
4. **Cancel**; **Sunday / the all-day-closed Friday**; **running late** (needs an appointment *today*: book one first).
5. **Pricing / hours / prep / "do you detail a boat?"** -> answers, logs a note.
6. **"You overcharged me $60"** -> empathizes, collects name + number, hands off. Sheet shows `Needs Callback = YES`.
7. Cancel someone else's appointment with a wrong phone -> refused.

## Troubleshooting

| You see | Cause / fix |
|---|---|
| `No time zone found with key ...` | Windows has no timezone data: `pip install tzdata` (already in requirements.txt) |
| `Configuration problems: ...` at startup | `.env` is missing a value or `service_account.json` isn't where `.env` says. The message lists each one. |
| `HttpError 404 ... Not Found` on the calendar | Wrong `GOOGLE_CALENDAR_ID`, or the calendar isn't shared with the service account |
| `HttpError 403 ... insufficient/ forbidden` | Calendar shared as "view only" (needs "Make changes to events"), or the Sheet isn't shared as Editor |
| `... API has not been used in project ...` | Enable the Calendar / Sheets API in Google Cloud and wait a minute |
| `401 authentication_error` | `ANTHROPIC_API_KEY` is wrong |
| Agent says "booked" but nothing on the calendar | Impossible by design (it needs an `ok` tool result); check the server log for the tool call |
| Calendar is right but the Sheet row is missing | A Sheets write failed after the booking; the server log shows `Sheet write failed` |

## Known limitations
- The booking lock is per-process. Running several server processes would need a database lock or calendar-side uniqueness.
- Conversations live in memory; restarting the server ends them (the Calendar and Sheet keep everything that matters).
- Phone numbers are US-style, 10 digits.
- Not tested against live Google/Claude accounts at the time of writing; run the scenarios once yourself.

## Phase 2 notes (Vapi)
`Agent.reply()` doesn't care where the text comes from. Expose the same tools to Vapi (server-side tools via webhook) or point Vapi's custom-LLM at an endpoint wrapping `Agent`. Latency levers: short prompts and replies, a fast model, streaming, small tool results, moving Sheet logging off the reply path, and Vapi's endpointing and filler-phrase settings. Callers say numbers aloud ("four one five..."), so the model must write digits.
