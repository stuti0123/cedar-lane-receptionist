# Cedar Lane Auto Detailing: AI Receptionist (Phase 1 + Phase 2)

An LLM-powered receptionist for Cedar Lane Auto Detailing. Phase 1 provides text chat; Phase 2 adds real-time voice calls through **Vapi**. The same agent books, reschedules and cancels appointments on **Google Calendar**, records caller details and call outcomes in **Google Sheets**, answers shop questions, and hands off issues that need a person.

The LLM handles conversation and tool selection. Deterministic Python code enforces scheduling, availability, identity and booking-confirmation rules.

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
| `app/llm.py` | Provider adapters for Gemini, Groq (OpenAI-compatible) and Anthropic. Pick with `LLM_PROVIDER` in `.env`. |
| `app/gcal.py`, `app/sheets.py` | Google adapters (service account, automatic retries on transient errors). |
| `app/wiring.py` | Builds the real agent (validates config, creates Sheet tabs). |
| `app/vapi.py` | Phase 2: the endpoint Vapi calls (streams the agent's answer back). |
| `app/server.py`, `static/index.html` | FastAPI server, `/chat`, `/health`, bare chat page. Conversations expire after 1h idle (`app/sessions.py`). |
| `app/cli.py` | Chat in the terminal and see the agent's tool calls. |
| `app/seed.py` | Puts test events on the calendar, creates the Sheet tabs. |
| `scripts/simulate_call.py` | Plays a scripted phone call against `/vapi/chat/completions` (no Vapi credits needed) and prints each answer with timing. |

### Key design decisions

1. **The LLM never decides availability.** It must call `check_availability`; code computes it. The prompt forbids claiming a booking without an `ok` tool result.
2. **Business rules live in code, not the prompt.** Hours, durations, notice, conflicts, phone validation and "can't touch someone else's booking" are enforced in `booking.py` and related validation code, so a caller can't talk the model out of them.
3. **Conflicts count concurrency, not events** (`peak_overlap`): with 2 bays, two events that overlap my slot but not each other still leave a bay free. `BAYS = 1` by default, matching the assignment's example.
4. **Any event on the calendar blocks time**, including all-day ones (whole day blocked) and events not made by the agent. Events marked "free" are ignored.
5. **Race safety**: availability is re-checked inside a lock immediately before the insert.
6. **Identity = phone number**, stored on the event (`extendedProperties.private.phone`) so look-ups are a server-side filter, and as the Contacts key. Reschedule/cancel/late require the matching phone; "not found" and "not yours" return the same error.
7. **Sheet failures never undo a booking** (logged server-side instead).
8. **Handoff** = `handoff_to_human`: needs a valid callback number, sets `Needs Callback = YES` and writes a flagged CallLog row. No live transfer exists in a text chat.

### Sheet layout
- **Contacts**: one row per caller with a phone number — Phone, Name, Vehicle, First Contact, Last Contact, Total Calls, Needs Callback and History.
- **CallLog**: Timestamp, Session, Phone, Name, Topic, Summary and Handoff. Records logged call actions and notes, including handoffs when a caller has not provided a phone number.

### What it handles vs hands off
Handles: book, reschedule, cancel, running late (15-min grace), pricing, hours, prep requirements, "do you detail X?".
Hands off: charge complaints/refunds, custom quotes (ceramic/paint correction), unsupported or unsure vehicles, damage claims, angry callers, anything not in `shop.py`.

## Setup

1. **Google Cloud**: create a project (no billing or free trial needed) and enable the *Google Calendar API* and *Google Sheets API*.
2. **Google sign-in file.** Pick ONE:
   - **A. OAuth "Desktop app" (use this if service-account keys are blocked by an organisation policy).** APIs & Services -> OAuth consent screen / Google Auth Platform: app name, your email, audience **External**, add yourself as a **test user**. Then Clients -> Create client -> **Desktop app** -> download the JSON and save it as `oauth_client.json` in the project root. Works on your own calendar and sheet; no sharing needed.
   - **B. Service account.** IAM & Admin -> Service accounts -> Create -> Keys -> Add key -> JSON. Save as `service_account.json`, then share the calendar ("Make changes to events") and the Sheet (Editor) with the service account's email.
   Never commit either file (both are gitignored).
3. **Calendar**: in Google Calendar create a new calendar "Cedar Lane Bookings". Settings for that calendar -> *Integrate calendar* -> copy the **Calendar ID**.
4. **Sheet**: create a new spreadsheet. Copy the ID from the URL (`/d/<THIS PART>/edit`).
5. **AI provider:** Gemini is the default and has a free tier; create a key at https://aistudio.google.com/apikey. Groq is also supported (`LLM_PROVIDER=groq`, `GROQ_API_KEY`, and `GROQ_MODEL`); Anthropic is supported with `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` (paid). Use only made-up test data with free-tier services and review their data-use terms.
6. Copy `.env.example` to `.env`. Set `LLM_PROVIDER` and the corresponding provider key, plus `GOOGLE_APPLICATION_CREDENTIALS` (your credentials file), `GOOGLE_CALENDAR_ID` and `GOOGLE_SHEET_ID`.
7. Install and run (Git Bash on Windows shown; on Mac/Linux use `source .venv/bin/activate`):
   ```bash
   python -m venv .venv
   source .venv/Scripts/activate
   pip install -r requirements.txt
   python -m pytest              # offline tests; no live Google or LLM calls
   python -m app.login           # OAuth only: one-time browser sign-in (skip for a service account)
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
| "Service account key creation is disabled" | An organisation policy blocks keys. Use option A (OAuth) above instead |
| "Not signed in to Google yet" / "sign-in has expired" | Run `python -m app.login`. While the consent screen is in *Testing*, Google expires the sign-in after about 7 days, so just run it again |
| "Access blocked" / "Google hasn't verified this app" in the browser | Add your Gmail as a *test user* on the consent screen; then click Advanced -> continue |
| `HttpError 404 ... Not Found` on the calendar | Wrong `GOOGLE_CALENDAR_ID`, or the calendar isn't shared with the service account |
| `HttpError 403 ... insufficient/ forbidden` | Calendar shared as "view only" (needs "Make changes to events"), or the Sheet isn't shared as Editor |
| `... API has not been used in project ...` | Enable the Calendar / Sheets API in Google Cloud and wait a minute |
| `API key not valid` / 400 or 403 from Gemini | `GEMINI_API_KEY` is wrong, or not saved in `.env` |
| `429` / `RESOURCE_EXHAUSTED` from Gemini | Free-tier rate limit: wait a minute and retry, or set `GEMINI_MODEL` to another free model |
| Voice replies "Sorry, I'm having trouble right now" and the log shows `429` from Groq | Free-tier tokens-per-minute cap. Use `LLM_PROVIDER=gemini`. |
| Log shows `400 ... tool call validation failed` | The model garbled its own tool call. The Groq adapter retries it automatically; Gemini does not have this problem. |
| `.env` says one provider but the log shows another | `.env` wins (`load_dotenv(override=True)`), but restart the server after editing it; it is only read at startup. |
| `404 ... model not found` | Change `GEMINI_MODEL` in `.env` to a model listed at ai.google.dev/gemini-api/docs/pricing |
| `401 authentication_error` (Claude only) | `ANTHROPIC_API_KEY` is wrong |
| Agent says "booked" but nothing on the calendar | Impossible by design (it needs an `ok` tool result); check the server log for the tool call |
| Calendar is right but the Sheet row is missing | A Sheets write failed after the booking; the server log shows `Sheet write failed` |

## Known limitations
- The booking lock is per-process. Running several server processes would need a database lock or calendar-side uniqueness.
- Conversations live in memory; restarting the server ends them (the Calendar and Sheet keep everything that matters).
- Phone numbers are US-style, 10 digits.
- Automated tests use fakes and do not require live Google or LLM credentials. The live Vapi/Gemini booking flow has also been demonstrated separately; use your own test calendar and made-up caller details when reproducing it.

## Phase 2: voice with Vapi

Vapi handles the phone/microphone, speech-to-text and text-to-speech. We run the brain. For every sentence the
caller says, Vapi POSTs the conversation to `<public url>/vapi/chat/completions` ("custom LLM", OpenAI-style).
`app/vapi.py` passes the caller's latest sentence to the **same agent** as the text chat and streams the answer back
in small pieces. The agent can send a short filler while a lookup runs (`Agent.stream(..., interim=True)`). Text chat is unaffected (`reply()` returns only the final answer). Vapi requests are debounced and marked cancelled when the client disconnects; an already-running synchronous model request cannot be forcibly stopped.

### Setup
1. Create a Vapi account at dashboard.vapi.ai (new accounts get starter credits; check their pricing page).
2. In `.env`, select a Gemini model available to your API key and set a random `VAPI_SECRET=...`. Optional voice tuning (all off or default unless you set them): `GEMINI_STREAM=1` (stream Gemini's words as they arrive) and `GEMINI_THINKING_LEVEL`, which were both *slower* in my measurements, so the demo leaves them unset; and `VAPI_DEBOUNCE_SECONDS` (default 0.5; Vapi re-sends on every speech fragment, so the model starts a beat late and skips requests Vapi abandons). Keep the same secret in the Vapi Custom LLM credential.
3. Start the server: `python -m uvicorn app.server:app --port 8000`
4. Make it reachable from the internet: install ngrok, then `ngrok http 8000`. Copy the `https://....ngrok-free.app` URL.
5. In Vapi: *Integrations -> Custom LLM* -> add a credential whose API key is your `VAPI_SECRET` (Vapi sends it as `Authorization: Bearer ...`).
6. Create an assistant: Model -> **Custom LLM**, URL = `https://....ngrok-free.app/vapi` (Vapi adds `/chat/completions` itself), model name anything (e.g. `cedar-lane`).
   First message: "Thanks for calling Cedar Lane Auto Detailing. How can I help?" The system prompt typed in Vapi is ignored; ours lives in `app/agent.py`.
7. Latency settings to tune (names from Vapi's docs; check the dashboard for current wording): `startSpeakingPlan.waitSeconds` (default 0.4, lower = faster but may cut people off),
   smart endpointing (LiveKit for English), `stopSpeakingPlan` (`numWords` 0, `voiceSeconds` 0.2, `backoffSeconds` 1), and a fast voice provider.
8. Before spending Vapi credits, rehearse with `python scripts/simulate_call.py` while the server runs (it books on the real Calendar, so delete the test event afterwards).
9. Talk to it from the dashboard (web call) or a Vapi phone number, and record.

### Latency: measured, and honestly short of the 1.2 s target
Vapi's call log shows per-turn latency; the server log shows each model call, tool result and "first words after X s".
Measured on the free Gemini tier (`gemini-3.5-flash-lite`) from India through ngrok:

| Turn type | Server time to first words | Full answer |
|---|---|---|
| Ordinary turn (no tool) | about 2-3 s | about 2-5 s |
| Turn that calls the Calendar (check or book) | about 2-3 s | about 4-8 s |

Where the time goes: each model call took roughly 1-4 s from my network, and each Calendar lookup or booking a few more seconds, on top of Vapi's speech-to-text, endpointing and voice. What I tried: Gemini streaming and lower thinking levels (both slower here), shorter and timed filler phrases, a request debounce, cancelling abandoned requests, and a Groq model (fast, but the free tier allows only about two calls per minute for this agent: ~3k input tokens per call against a 7-8k tokens/minute cap, so it is not usable for a live call). To get under 1.2 s I would use a paid or faster model endpoint closer to the caller, a smaller prompt and tool list, a cached view of the calendar, and a deployment near the Google APIs; I did not do these because the brief asked for free tiers.

### Phase 2 limits
- The ngrok URL changes each time you restart ngrok (update it in Vapi). The server must be running during the call.
- Cancellation checks prevent stale responses and can skip tools that have not started, but cannot undo a Calendar/Sheets operation that has already completed or stop a synchronous provider request already in flight.
- Call sessions are held in memory and expire after one hour of inactivity; restarting the server clears active conversation history.
- Voice booking requires the caller to confirm the spoken-back service, date, time and phone number. Phone numbers are US-style, 10 digits.
