import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

DATABASE_URL = os.getenv("CRBS_DATABASE_URL", "sqlite:///" + str(PROJECT_ROOT / "crbs.db"))

ENVIRONMENT = os.getenv("CRBS_ENV", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT == "production"

# Overrides request.base_url when the app is reverse-proxied under a path
# prefix (e.g. nginx serving it at /crbs/ alongside another app) — the app
# itself never sees that prefix, so links built from the request alone
# would point at the wrong URL. Leave unset when the app owns its domain
# outright (e.g. Render), where request.base_url is already correct.
PUBLIC_BASE_URL = os.getenv("CRBS_PUBLIC_BASE_URL", "").strip().rstrip("/")


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

# Microsoft Entra ID (Azure AD) single sign-on. Optional: the "Sign in with
# Microsoft" button only appears once all three are set. Register the app in
# the UNDP tenant with redirect URI <base-url>/auth/microsoft/callback.
MS_TENANT_ID = os.getenv("CRBS_MS_TENANT_ID", "").strip()
MS_CLIENT_ID = os.getenv("CRBS_MS_CLIENT_ID", "").strip()
MS_CLIENT_SECRET = os.getenv("CRBS_MS_CLIENT_SECRET", "").strip()
SSO_ENABLED = bool(MS_TENANT_ID and MS_CLIENT_ID and MS_CLIENT_SECRET)

# Outbound mail for account-creation and password-reset notices. Optional: when
# unset, the temporary password is shown on-screen to the admin instead (today's
# behaviour), so this is safe to leave off.
SMTP_HOST = os.getenv("CRBS_SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("CRBS_SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("CRBS_SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("CRBS_SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("CRBS_SMTP_FROM", "").strip()
EMAIL_ENABLED = bool(SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM)

ORGANISATION_NAME = os.getenv("CRBS_ORG_NAME", "UNDP Country Office")
DUTY_STATION = os.getenv("CRBS_DUTY_STATION", "Khartoum, Sudan")
CURRENCY = os.getenv("CRBS_CURRENCY", "USD")
TIMEZONE_LABEL = os.getenv("CRBS_TZ_LABEL", "EAT (UTC+2)")
# IANA identifier, distinct from the display-only TIMEZONE_LABEL above — this
# one has to be something Microsoft Graph actually understands.
TZ_NAME = os.getenv("CRBS_TZ_NAME", "Africa/Khartoum")

# Sync approved bookings to a shared Outlook room calendar via Microsoft Graph.
# Reuses the Entra ID app registration set up for SSO — that registration
# additionally needs the Calendars.ReadWrite *application* permission (tenant
# admin consent required) for this to work. Optional: stays inactive without
# Fallback mailbox used only for rooms that don't have their own email set.
ROOM_CALENDAR_EMAIL = os.getenv("CRBS_ROOM_CALENDAR_EMAIL", "").strip()
CALENDAR_SYNC_ENABLED = bool(MS_TENANT_ID and MS_CLIENT_ID and MS_CLIENT_SECRET)

SESSION_MAX_AGE_SECONDS = 60 * 60 * 8
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LENGTH = 10

# Booking rules
BOOKING_BUFFER_MINUTES = 15          # set-up / tear-down gap enforced between bookings
MAX_BOOKING_HOURS = 12
ADVANCE_BOOKING_DAYS = 365
