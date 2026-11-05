"""Measures how fast Gemini answers with the REAL receptionist prompt, with no Vapi in the way.
Run:  python -m app.latency_test            (takes about a minute; uses a handful of free-tier requests)
It prints, per model and thinking setting: seconds until the first words, and seconds until the full answer."""
import statistics
import sys
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from . import config  # noqa: E402
from .agent import system_prompt  # noqa: E402
from .llm import GeminiLLM  # noqa: E402

QUESTION = "What are your opening hours?"
RUNS = 3


def measure(llm, system):
    history = [llm.user_message(QUESTION)]
    t0 = time.monotonic()
    llm.complete(history, system)
    whole = time.monotonic() - t0
    t0, first = time.monotonic(), None
    gen = llm.complete_stream(history, system)
    try:
        while True:
            next(gen)
            first = first or time.monotonic() - t0
    except StopIteration:
        pass
    return first or time.monotonic() - t0, whole


def main():
    system = system_prompt(datetime.now(config.SHOP_TZ))
    models = sys.argv[1:] or sorted({config.GEMINI_MODEL, "gemini-3.5-flash-lite", "gemini-3.6-flash"})
    print(f"question: {QUESTION!r}, {RUNS} runs each (first-words seconds / full-answer seconds)\n")
    for model in models:
        for level in ("", "MINIMAL"):
            config.GEMINI_THINKING_LEVEL = level
            llm = GeminiLLM(model=model)
            try:
                results = [measure(llm, system) for _ in range(RUNS)]
            except Exception as e:  # a model may be busy or not accept a setting: say so and move on
                print(f"{model:24} thinking={level or 'default':8} FAILED: {str(e)[:90]}")
                continue
            first = statistics.median(r[0] for r in results)
            whole = statistics.median(r[1] for r in results)
            print(f"{model:24} thinking={level or 'default':8} first words {first:4.1f}s   full answer {whole:4.1f}s")


if __name__ == "__main__":
    main()
