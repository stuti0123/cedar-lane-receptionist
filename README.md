# Cedar Lane Auto Detailing: AI receptionist (Phase 1: text chat)

An LLM agent (Claude, tool use) that books, moves and cancels detailing appointments on **Google Calendar**,
keeps one row per caller in **Google Sheets**, answers shop questions, and hands off what it shouldn't handle.

## Architecture (read in this order)

```
browser chat -> POST /chat -> agent.py (LLM loop) -> tools.py (thin wrappers) -> booking.py (ALL the rules)
                                                                                    |-> availability.py (pure logic)
                                                                                    |-> gcal.py   (Google Calendar)
                                                          tools.py ------------------> sheets.py (Google Sheets)
```

| File | Job |
|---|---|
| `app/shop.py` | Services, durations, prices, hours, prep, vehicle policy. **One source of truth**: feeds both the scheduling logic and the prompt. Prices/policies are placeholders. |
| `app/availability.py` | Pure functions: is this slot bookable? (hours, 30-min grid, 1h notice, bay concurrency). No I/O. |
| `app/booking.py` | Check / book / find / reschedule / cancel / late. Re-checks availability right before writing, serializes check+insert with a lock, verifies the caller owns an appointment (phone match) before changing it. |
| `app/tools.py` | Tool schemas the LLM sees + dispatcher. Logs to the Sheet after each successful action. |
| `app/agent.py` | System prompt + tool-use loop (max 8 tool steps per message). |
| `app/gcal.py`, `app/sheets.py` | Google adapters (service account). |
| `app/seed.py` | Puts test events on the calendar, creates the Sheet tabs. |

### Key design decisions (be ready to defend these)

1. **The LLM never decides availability.** It must call `check_availability`; code computes it. Prompt rules say never claim a booking without an `ok` tool result.
2. **Business rules live in code, not the prompt.** Hours, durations, notice, conflicts, "can't touch someone else's booking" are enforced in `booking.py`, so a clever caller can't talk the model out of them.
3. **Conflicts count concurrency, not events** (`peak_overlap`): with 2 bays, two events that overlap my slot but not each other still leave a bay free. `BAYS = 1` by default, matching the example.
4. **Any event on the calendar blocks time**, including all-day ones (whole day blocked) and events not made by the agent. Events marked "free" are ignored.
5. **Race safety**: availability is re-checked inside a lock immediately before `insert`. Limitation: the lock is per-process; with multiple server processes you'd need a DB lock or calendar-side uniqueness.
6. **Identity = phone number.** Stored on the event (`extendedProperties.private.phone`) so look-ups are a server-side filter, and as the Contacts key. Reschedule/cancel/late require the matching phone; "not found" and "not yours" return the same error.
7. **Sheet failures never undo a booking** (`_write_sheet` swallows+logs). Trade-off: a booking could exist without its row. It is logged in the server log.
8. **Handoff** = `handoff_to_human`, which needs a valid callback number, writes `Needs Callback = YES` on the Contacts row and a flagged CallLog row. There is no live transfer in a text chat.

### Sheet layout
- **Contacts**: Phone, Name, Vehicle, First Contact, Last Contact, Total Calls, Needs Callback, History (a running log, newest last).
- **CallLog**: Timestamp, Session, Phone, Name, Topic, Summary, Handoff (one row per event; covers anonymous callers).

### What it handles vs hands off
Handles: book, reschedule, cancel, running late (grace 15 min), pricing, hours, prep requirements, vehicle supported?
Hands off: charge complaints/refunds, custom quotes (ceramic/paint correction), unsupported or unsure vehicles, damage claims, angry callers, anything not in `shop.py`.

## Setup (about 15 minutes)

1. **Google Cloud**: create a project, enable *Google Calendar API* and *Google Sheets API*.
2. **Service account**: IAM -> Service accounts -> create -> Keys -> add JSON key. Save as `service_account.json` in the repo root (gitignored). Note its email.
3. **Calendar**: in Google Calendar create a new calendar "Cedar Lane Bookings". Settings -> *Share with specific people* -> add the service-account email with **Make changes to events**. Copy the *Calendar ID* (Integrate calendar).
4. **Sheet**: create a spreadsheet, share it with the service-account email as **Editor**, copy the ID from the URL.
5. `cp .env.example .env` and fill in `ANTHROPIC_API_KEY`, `GOOGLE_CALENDAR_ID`, `GOOGLE_SHEET_ID`.
6. ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python -m app.seed            # test events + Sheet tabs
   pytest                        # 29 offline tests
   uvicorn app.server:app --reload   # open http://localhost:8000
   ```

## Scripts to try (seed prints the real dates)
1. **Assignment flow**: "Is 3:30 on Thursday open for an interior detail and wash?" -> taken, offers 4:30 -> "sure" -> name/phone -> booked. Check Calendar + Sheet.
2. **Reschedule**: call as 415-555-0122 (Maria): move her Thursday full detail to Tuesday 11am.
3. **Conflict on reschedule**: move to a taken time -> refuses and offers alternatives.
4. **Cancel**, **Sunday / Friday (closed all day)**, **running late** (needs an appointment *today*: book one first).
5. **Pricing / hours / prep / "do you detail a boat?"** -> answers, logs a note.
6. **"You overcharged me $60"** -> empathizes, collects name + number, hands off. Sheet shows `Needs Callback = YES`.
7. Try to cancel someone else's appointment with the wrong phone -> refused.

## Not verified here
The pure logic, tools and agent loop are covered by offline tests with fakes. The Google adapters and the live LLM were **not** run against real accounts when this was written, so do the setup above and run scripts 1-6 once before recording the video.

## Phase 2 notes (Vapi)
`Agent.reply()` is transport-agnostic. Expose the same tools as Vapi server-side tools (webhook) or point Vapi's custom-LLM at an endpoint wrapping `Agent`. Latency levers: short prompts and replies, a fast model, streaming, tool calls that return small JSON, Vapi endpointing/`waitSeconds` tuning and filler phrases. Spoken phone numbers ("four one five five five five oh one ninety") need normalizing; `normalize_phone` already accepts digits only, so have the LLM write digits.
