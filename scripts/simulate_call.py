"""Practise a phone call WITHOUT spending Vapi credits.

Start the server (uvicorn app.server:app --port 8000) in one terminal, then in another:
    python scripts/simulate_call.py
It posts each line below to /vapi/chat/completions exactly like Vapi does, reads the streamed answer and prints it
with timing. Edit SCRIPT to try other calls. Use a made-up phone number. It books on your real Calendar, so
delete the test event afterwards (or reseed)."""
import json
import os
import sys
import time
import urllib.request
import uuid

URL = os.getenv("SIM_URL", "http://127.0.0.1:8000/vapi/chat/completions")
SECRET = os.getenv("VAPI_SECRET", "")  # only if your server has VAPI_SECRET set; never printed

SCRIPT = [
    "Hi, can I get an interior detail and wash this Thursday at three thirty?",
    "Yes, four thirty works.",
    "Sara.",
    "four one five, five five five, zero one nine zero",
    "Yes, that's right.",
]


def ask(call_id: str, history: list) -> tuple[str, float, float]:
    payload = {"model": "cedar-lane", "stream": True, "call": {"id": call_id}, "messages": history}
    req = urllib.request.Request(URL, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    if SECRET:
        req.add_header("Authorization", f"Bearer {SECRET}")
    t0, first, text = time.monotonic(), None, ""
    with urllib.request.urlopen(req, timeout=60) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            delta = json.loads(line[5:])["choices"][0]["delta"].get("content")
            if delta:
                first = first or time.monotonic() - t0
                text += delta
    return text.strip(), first or 0.0, time.monotonic() - t0


def main() -> None:
    call_id, history = "sim-" + uuid.uuid4().hex[:8], []
    for line in SCRIPT:
        history.append({"role": "user", "content": line})
        print(f"CALLER: {line}")
        try:
            answer, first, total = ask(call_id, history)
        except Exception as e:
            sys.exit(f"request failed: {e}  (is the server running?)")
        history.append({"role": "assistant", "content": answer})
        print(f"AGENT : {answer}\n        [first words {first:.1f}s, full answer {total:.1f}s]\n")


if __name__ == "__main__":
    main()
