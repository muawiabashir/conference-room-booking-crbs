import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

DATABASE_URL = os.getenv("CRBS_DATABASE_URL", "sqlite:///" + str(PROJECT_ROOT / "crbs.db"))

ENVIRONMENT = os.getenv("CRBS_ENV", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT == "production"


def _load_secret_key():
    key = os.getenv("CRBS_SECRET_KEY")
    if key:
        return key
    if IS_PRODUCTION:
        raise RuntimeError(
            "CRBS_SECRET_KEY must be set when CRBS_ENV=production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    # Development: persist a random key so sessions survive a restart without
    # shipping a guessable default in source control.
    key_file = PROJECT_ROOT / ".secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(48))
        key_file.chmod(0o600)
    return key_file.read_text().strip()


SECRET_KEY = _load_secret_key()

# Session cookies are TLS-only in production; relaxed locally so http://127.0.0.1 works.
SESSION_HTTPS_ONLY = IS_PRODUCTION
SHOW_DEMO_CREDENTIALS = not IS_PRODUCTION

ORGANISATION_NAME = os.getenv("CRBS_ORG_NAME", "UNDP Country Office")
DUTY_STATION = os.getenv("CRBS_DUTY_STATION", "Khartoum, Sudan")
CURRENCY = os.getenv("CRBS_CURRENCY", "USD")
TIMEZONE_LABEL = os.getenv("CRBS_TZ_LABEL", "EAT (UTC+2)")

SESSION_MAX_AGE_SECONDS = 60 * 60 * 8
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LENGTH = 10

# Booking rules
BOOKING_BUFFER_MINUTES = 15          # set-up / tear-down gap enforced between bookings
MAX_BOOKING_HOURS = 12
ADVANCE_BOOKING_DAYS = 365
