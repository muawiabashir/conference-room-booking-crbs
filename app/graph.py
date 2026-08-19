"""Sync approved bookings to a shared Outlook room calendar via Microsoft Graph.

Uses the OAuth2 client-credentials flow (app-only auth, no signed-in user
involved) against the same Entra ID app registration used for SSO. That
registration additionally needs the Calendars.ReadWrite *application*
permission, with tenant admin consent, to create/delete events on
ROOM_CALENDAR_EMAIL's calendar.

Best-effort throughout: a Graph failure is logged and reported back to the
caller, but never raised — it must never block the booking action that
triggered it.
"""
import logging
import time

import httpx

from .config import (
    CALENDAR_SYNC_ENABLED, MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID, ROOM_CALENDAR_EMAIL,
    TZ_NAME,
)

logger = logging.getLogger("crbs.graph")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_token_cache = {"value": None, "expires_at": 0.0}


def _get_token():
    now = time.time()
    if _token_cache["value"] and _token_cache["expires_at"] - 60 > now:
        return _token_cache["value"]
    resp = httpx.post(
        "https://login.microsoftonline.com/%s/oauth2/v2.0/token" % MS_TENANT_ID,
        data={
            "grant_type": "client_credentials",
            "client_id": MS_CLIENT_ID,
            "client_secret": MS_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["value"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3600)
    return _token_cache["value"]


def _event_body(booking):
    lines = [
        "Booked via Conference Room Booking & Cost Recovery System.",
        "Reference: %s" % booking.reference,
        "Room: %s" % booking.room.name,
        "Counterpart: %s" % booking.organization.name,
        "Requested by: %s" % booking.requester.full_name,
    ]
    if booking.purpose:
        lines.append("Purpose: %s" % booking.purpose)
    return {
        "subject": "%s — %s (%s)" % (booking.title, booking.room.name, booking.reference),
        "body": {"contentType": "text", "content": "\n".join(lines)},
        "start": {"dateTime": booking.starts_at.isoformat(), "timeZone": TZ_NAME},
        "end": {"dateTime": booking.ends_at.isoformat(), "timeZone": TZ_NAME},
        "location": {"displayName": booking.room.name},
        "isReminderOn": False,
    }


def create_event(booking):
    """Returns the Graph event id on success, None on failure or when disabled."""
    if not CALENDAR_SYNC_ENABLED:
        return None
    try:
        resp = httpx.post(
            "%s/users/%s/events" % (GRAPH_BASE, ROOM_CALENDAR_EMAIL),
            headers={"Authorization": "Bearer %s" % _get_token()},
            json=_event_body(booking), timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["id"]
    except httpx.HTTPError:
        logger.exception("Failed to create Graph calendar event for booking %s", booking.reference)
        return None


def delete_event(event_id: str) -> bool:
    """Returns True on success (including "already gone"), False on failure."""
    if not CALENDAR_SYNC_ENABLED or not event_id:
        return False
    try:
        resp = httpx.delete(
            "%s/users/%s/events/%s" % (GRAPH_BASE, ROOM_CALENDAR_EMAIL, event_id),
            headers={"Authorization": "Bearer %s" % _get_token()},
            timeout=15,
        )
        if resp.status_code not in (204, 404):
            resp.raise_for_status()
        return True
    except httpx.HTTPError:
        logger.exception("Failed to delete Graph calendar event %s", event_id)
        return False
