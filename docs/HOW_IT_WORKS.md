# How the Cedar Lane AI receptionist works (plain English)

## The one-paragraph version
Cedar Lane is a small car-detailing shop that gets about 30 calls a day. This project is an AI receptionist that
answers those calls. It books, moves and cancels appointments on the shop's Google Calendar, writes down who called
and what they wanted in a Google Sheet, answers questions about prices and hours, and passes tricky things (like
billing complaints) to a human. This first phase is a text chat; the second phase turns it into a phone voice.

## The best way to picture it
Think of a restaurant host:
- The **AI model (Claude)** is the host's *judgement and conversation*: it understands what the guest wants and talks politely.
- The **reservation book** is Google Calendar. The host never trusts memory; they always look in the book.
- The **guest list notebook** is Google Sheets: one line per guest, plus notes on what happened.
- The **house rules** (opening hours, how long each service takes, who may change which booking) are written in code,
  not left to the host's mood. The AI can't break them even if a caller is persuasive.

## What happens in one chat (step by step)
1. The caller types a message.
2. The program sends the whole conversation to Claude, along with a short list of "actions" it is allowed to take
   (check availability, book, reschedule, cancel, note running late, log a note, hand off to a human).
3. Claude either replies in words, or asks for an action ("check Thursday 3:30 for an interior detail").
4. The program runs that action against Google Calendar/Sheets and gives Claude the result.
5. Claude turns the result into a short, friendly sentence. If it needs another action, go back to step 3.

Example: *"Is 3:30 Thursday open?"* -> Claude asks to check -> the program looks at the calendar and finds a booking
there -> Claude says "That one's full, 4:30 is open, want that?" -> caller agrees -> Claude asks for name and number ->
asks the program to book -> the program re-checks the slot is still free, writes the event, adds the caller to the Sheet.

## Who is responsible for what
| Job | Done by | Why |
|---|---|---|
| Understanding the caller, sounding human | The AI | It is good at language |
| Deciding if a time is free | **Code** | A wrong "yes" means a double booking, so we don't leave it to a guess |
| Opening hours, durations, 1-hour notice | **Code** | Rules must never bend |
| Making sure only the right person changes a booking | **Code** (matches phone number) | Privacy and safety |
| Remembering bookings | Google Calendar | The shop already uses it |
| Remembering callers | Google Sheets | Easy for staff to read |

## What it can and can't do
**Handles on its own:** book, move, cancel; "I'm running late"; prices, hours, prep instructions; "do you do my kind of vehicle?".

**Hands to a human** (records the caller's name, number and the issue, marks "Needs Callback" in the Sheet):
complaints or disputes about a charge, custom quotes (ceramic coating, paint correction), vehicles the shop might not
handle, damage claims, angry callers, anything it doesn't know. It never promises refunds or outcomes.

## The safety rules we built in
1. **It must look before it speaks.** It can't say a slot is free, or a booking is done, without a real result from the calendar.
2. **Double-check at the last moment.** Just before saving, availability is checked again, so two people can't grab the same slot.
3. **Your phone number is your key.** To change or cancel an appointment you must give the number it was booked under.
   Wrong number or someone else's booking gives the same "not found" answer, so nobody can probe other people's bookings.
4. **A Sheet problem never ruins a booking.** If the notebook is unavailable, the booking still stands and the problem is logged.
5. **No endless loops.** The AI gets at most 8 actions per message; idle conversations expire after an hour.

## What's in the project (one line each)
- `shop.py`: the shop's facts (services, prices, hours, rules). Change the shop here.
- `availability.py`: the "is this time free?" maths. No internet, fully tested.
- `booking.py`: the rules for booking, moving, cancelling.
- `tools.py`: the list of actions the AI may request, and the glue to run them.
- `agent.py`: the conversation with Claude, plus the instructions that shape its behaviour.
- `gcal.py` / `sheets.py`: talk to Google Calendar / Google Sheets.
- `server.py`, `static/index.html`: a small website to chat with it. `cli.py`: chat in the terminal.
- `seed.py`: fills the calendar with sample appointments for testing.
- `tests/`: 36 automatic checks (no internet needed) that prove the rules hold.

## Show it (a 3-minute demo script)
1. Open the Calendar and the Sheet side by side with the chat.
2. "Is 3:30 on Thursday open for an interior detail and wash?" -> "full", offers 4:30.
3. "Sure" -> give name and number -> booked. Show the new event and the new Sheet row.
4. "I need to move that to Tuesday morning" -> give number -> moved. Show the calendar change.
5. "How much is a full detail for an SUV?" -> answers from the price list.
6. "You overcharged me last week" -> polite handoff; show **Needs Callback = YES** in the Sheet.

## Words you may hear
- **LLM / AI model**: the language program (here Claude) that understands and writes text.
- **Tool / function calling**: the AI asks the program to do something real (like look at the calendar) instead of guessing.
- **Service account**: a robot Google login for the program, which we share the calendar and Sheet with.
- **API**: the way one program asks another for things (here, asking Google for events).
- **Handoff**: passing the caller to a human with a clear note of what they need.

## Honest limits (good to say out loud)
- It is a text chat for now; voice comes in Phase 2.
- Prices, hours and policies are made-up examples, easy to change in `shop.py`.
- One server process only (the "last-moment check" lock lives inside it); a bigger setup would need a shared lock.
- Phone numbers are US-style (10 digits).

## Likely interview questions (with short answers)
- **Why not let the AI decide availability?** It can be confidently wrong. Code is exact and testable; the AI only talks.
- **How do you stop double booking?** Check again right before saving, inside a lock. With several servers I'd need a database lock.
- **What if Google Sheets is down?** The booking still succeeds, the failure is logged, and I'd add a retry queue.
- **How do you stop someone cancelling another person's appointment?** Every change needs the phone number the booking was made under.
- **What would you improve next?** Retry queue for Sheet writes, multi-bay capacity per service, structured logs/metrics, a real transfer to a human in Phase 2.
- **How did you test without live accounts?** The calendar and Sheet are swapped for in-memory fakes, and the AI for a scripted one; 36 tests cover rules, tools and the loop.
