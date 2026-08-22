"""Generate a downloadable .ics file for a single booking, so a requester can
add their room to a personal calendar (Outlook, Google, Apple) with one click.

Self-contained: unlike the Graph calendar sync, this needs no external API,
credentials, or admin consent — it's just a file the browser downloads.
"""
from datetime import timezone
from zoneinfo import ZoneInfo

from .config import TZ_NAME
from .models import utcnow

FOLD_WIDTH = 70


def _escape(text):
    return ((text or "")
            .replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n"))


def _fold(line):
    # RFC 5545: content lines should wrap by 75 octets, continuation lines
    # start with a space. Char-count based — fine for this app's ASCII-heavy
    # titles/purposes; a stray multi-byte run just folds a little early.
    if len(line) <= FOLD_WIDTH:
        return line
    parts = [line[:FOLD_WIDTH]]
    rest = line[FOLD_WIDTH:]
    while rest:
        parts.append(" " + rest[:FOLD_WIDTH - 1])
        rest = rest[FOLD_WIDTH - 1:]
    return "\r\n".join(parts)


def _stamp_utc(naive_local_dt):
    local = naive_local_dt.replace(tzinfo=ZoneInfo(TZ_NAME))
    return local.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def booking_ics(booking):
    confirmed = booking.status.value in ("APPROVED", "COMPLETED")
    desc_lines = [
        "Reference: %s" % booking.reference,
        "Room: %s" % booking.room.name,
        "Counterpart: %s" % booking.organization.name,
    ]
    if booking.purpose:
        desc_lines.append("Purpose: %s" % booking.purpose)
    if not confirmed:
        desc_lines.append("Status: awaiting facility approval — this hold is not yet confirmed.")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//UNDP Conference Room Booking & Cost Recovery//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        "UID:booking-%d@crbs.undp" % booking.id,
        "DTSTAMP:%s" % _stamp_utc(utcnow()),
        "DTSTART:%s" % _stamp_utc(booking.starts_at),
        "DTEND:%s" % _stamp_utc(booking.ends_at),
        _fold("SUMMARY:%s" % _escape(booking.title)),
        _fold("LOCATION:%s" % _escape(booking.room.name)),
        _fold("DESCRIPTION:%s" % _escape("\n".join(desc_lines))),
        "STATUS:%s" % ("CONFIRMED" if confirmed else "TENTATIVE"),
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


def availability_feed_ics(bookings):
    """Subscribable free/busy feed for all rooms combined — no meeting titles,
    purpose, counterpart or requester, matching the public calendar page's
    privacy stance. Meant to be added as an internet calendar subscription
    (webcal) in Outlook/Google/Apple, refreshed periodically by the calendar
    app itself rather than pushed.
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//UNDP Conference Room Booking & Cost Recovery//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Conference room availability",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for booking in bookings:
        confirmed = booking.status.value in ("APPROVED", "COMPLETED")
        lines += [
            "BEGIN:VEVENT",
            "UID:availability-%d@crbs.undp" % booking.id,
            "DTSTAMP:%s" % _stamp_utc(utcnow()),
            "DTSTART:%s" % _stamp_utc(booking.starts_at),
            "DTEND:%s" % _stamp_utc(booking.ends_at),
            _fold("SUMMARY:%s" % _escape("Busy – %s" % booking.room.name)),
            _fold("LOCATION:%s" % _escape(booking.room.name)),
            "STATUS:%s" % ("CONFIRMED" if confirmed else "TENTATIVE"),
            "TRANSP:OPAQUE",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
