"""Chat with the receptionist in the terminal (no browser needed):  python -m app.cli
Handy for quick tests, and the tool calls are printed so you can SEE what the agent decided."""
import logging
import uuid

from .wiring import build_agent


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="   [%(name)s] %(message)s")
    agent = build_agent()
    session = agent.new_session(f"cli-{uuid.uuid4().hex[:8]}")
    print("Cedar Lane receptionist. Type 'quit' to stop.\n")
    while True:
        try:
            text = input("You:   ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"quit", "exit"}:
            break
        if text:
            print(f"Agent: {agent.reply(session, text)}\n")


if __name__ == "__main__":
    main()
