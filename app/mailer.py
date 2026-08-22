"""Outbound account-notice email. Best-effort: a delivery failure never blocks
the admin action that triggered it — callers fall back to showing the
temporary password on-screen, same as when SMTP isn't configured at all.
"""
import logging
import smtplib
from email.message import EmailMessage

from .config import (
    EMAIL_ENABLED, ORGANISATION_NAME, SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USERNAME,
)

logger = logging.getLogger("crbs.mailer")


def send_mail(to_email: str, subject: str, body: str) -> bool:
    """Returns True on delivery, False on any failure (logged, never raised)."""
    if not EMAIL_ENABLED:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        logger.exception("Failed to send mail to %s", to_email)
        return False


def send_calendar_invite(to_email: str, subject: str, body: str, ics_text: str, ics_filename: str) -> bool:
    """Same delivery contract as send_mail(), with the booking's .ics attached
    so the recipient's mail client can add it to a personal calendar.
    """
    if not EMAIL_ENABLED:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.set_content(body)
    msg.add_attachment(ics_text.encode("utf-8"), maintype="text", subtype="calendar",
                       filename=ics_filename)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        logger.exception("Failed to send calendar invite to %s", to_email)
        return False


def send_account_email(to_email: str, full_name: str, temp_password: str, login_url: str,
                       is_reset: bool) -> bool:
    action = "Your password has been reset" if is_reset else "An account has been created for you"
    subject = "%s · %s" % (
        "Password reset" if is_reset else "Welcome",
        ORGANISATION_NAME,
    )
    body = (
        "Hello %s,\n\n"
        "%s on the %s Conference Room Booking & Cost Recovery system.\n\n"
        "Sign in at: %s\n"
        "Email address: %s\n"
        "Temporary password: %s\n\n"
        "You will be asked to choose a new password the first time you sign in.\n"
        "If you were not expecting this message, contact your system administrator.\n"
    ) % (full_name, action, ORGANISATION_NAME, login_url, to_email, temp_password)
    return send_mail(to_email, subject, body)
