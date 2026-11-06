# How the Cedar Lane AI receptionist works 

## The one-paragraph version
Cedar Lane is a small car-detailing shop that gets about 30 calls a day. This project is an AI receptionist that
answers those calls. It books, moves and cancels appointments on the shop's Google Calendar, writes down who called
and what they wanted in a Google Sheet, answers questions about prices and hours, and passes tricky things (like
billing complaints) to a human. This first phase is a text chat; the second phase turns it into a phone voice.

## The best way to picture it
Think of a restaurant host:
- The **AI model (Gemini by default, the AI model optional)** is the host's *judgement and conversation*: it understands what the guest wants and talks politely.
- The **reservation book** is Google Calendar. The host never trusts memory; they always look in the book.
- The **guest list notebook** is Google Sheets: one line per guest, plus notes on what happened.
- The **house rules** (opening hours, how long each service takes, who may change which booking) are written in code,
  not left to the host's mood. The AI can't break them even if a caller is persuasive.

## What happens in one chat (step by step)
1. The caller types a message.
2. The program sends the whole conversation to the AI model, along with a short list of "actions" it is allowed to take
   (check availability, book, reschedule, cancel, note running late, log a note, hand off to a human).
3. the AI model either replies in words, or asks for an action ("check Thursday 3:30 for an interior detail").
4. The program runs that action against Google Calendar/Sheets and gives the AI model the result.
5. the AI model turns the result into a short, friendly sentence. If it needs another action, go back to step 3.

Example: *"Is 3:30 Thursday open?"* -> the AI model asks to check -> the program looks at the calendar and finds a booking
there -> the AI model says "That one's full, 4:30 is open, want that?" -> caller agrees -> the AI model asks for name and number ->
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

## What's in the project 
- `shop.py`: the shop's facts (services, prices, hours, rules). Change the shop here.
- `availability.py`: the "is this time free?" maths. No internet, fully tested.
- `booking.py`: the rules for booking, moving, cancelling.
- `tools.py`: the list of actions the AI may request, and the glue to run them.
- `agent.py`: the conversation with the AI model, plus the instructions that shape its behaviour.
- `gcal.py` / `sheets.py`: talk to Google Calendar / Google Sheets.
- `server.py`, `static/index.html`: a small website to chat with it. `cli.py`: chat in the terminal.
- `seed.py`: fills the calendar with sample appointments for testing.
- `tests/`: 36 automatic checks (no internet needed) that prove the rules hold.
