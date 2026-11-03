"""One-time Google sign-in:  python -m app.login
Opens your browser, you approve access to your own Calendar and Sheets, and token.json is saved."""
from pathlib import Path

from . import config
from .gcal import login

if __name__ == "__main__":
    if not Path(config.GOOGLE_CREDENTIALS_FILE).is_file():
        raise SystemExit(f"File not found: {config.GOOGLE_CREDENTIALS_FILE} (check GOOGLE_APPLICATION_CREDENTIALS in .env)")
    login()
    print(f"Signed in. Saved {config.GOOGLE_TOKEN_FILE}. You can now run the seed script and the app.")
