"""US phone number helpers. The 10-digit string is the caller's identity key everywhere."""
import re


def normalize_phone(raw: str) -> str:
    """'(415) 555-0190', '+1 415-555-0190', '4155550190' -> '4155550190'. Raises ValueError otherwise."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("Phone number must be 10 digits (US).")
    return digits


def format_phone(digits: str) -> str:
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
