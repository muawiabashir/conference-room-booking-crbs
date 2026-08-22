"""Unauthenticated room-availability view, linked from the sign-in page.

Deliberately minimal: only start/end time and a PENDING/APPROVED-derived
label ever reach the template — never the booking object itself — so a
meeting's title, purpose, counterpart or requester can't leak here even by
a future template edit gone careless.
"""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..config import DUTY_STATION, ORGANISATION_NAME, TIMEZONE_LABEL
from ..database import get_db
from ..ics import availability_feed_ics
from ..models import ACTIVE_BOOKING_STATUSES, Booking, Room
from ..templating import templates

router = APIRouter()

DAY_START_HOUR = 8
DAY_END_HOUR = 19
FEED_PAST_DAYS = 7
FEED_FUTURE_DAYS = 180


@router.get("/public/calendar")
def public_calendar(request: Request, day: str = "", db: Session = Depends(get_db)):
    try:
        target = datetime.strptime(day, "%Y-%m-%d").date() if day else date.today()
    except ValueError:
        target = date.today()

    start = datetime.combine(target, datetime.min.time())
    end = start + timedelta(days=1)
    rooms = list(db.scalars(select(Room).where(Room.is_active.is_(True)).order_by(Room.name)))

    stmt = select(Booking).where(
        and_(Booking.starts_at < end, Booking.ends_at > start,
             Booking.status.in_(ACTIVE_BOOKING_STATUSES))
    )
    bookings = list(db.scalars(stmt))

    hours = list(range(DAY_START_HOUR, DAY_END_HOUR + 1))
    total_minutes = (DAY_END_HOUR - DAY_START_HOUR) * 60

    events = {}
    for booking in bookings:
        s = max(booking.starts_at, start.replace(hour=DAY_START_HOUR))
        e = min(booking.ends_at, start.replace(hour=DAY_END_HOUR))
        if e <= s:
            continue
        offset = (s - start.replace(hour=DAY_START_HOUR)).total_seconds() / 60
        length = (e - s).total_seconds() / 60
        events.setdefault(booking.room_id, []).append({
            "starts_at": s, "ends_at": e, "pending": booking.status.value == "PENDING",
            "top_pct": offset / total_minutes * 100,
            "height_pct": max(3.0, length / total_minutes * 100),
        })

    return templates.TemplateResponse(request, "public_calendar.html", {
        "target": target, "rooms": rooms, "hours": hours, "events": events,
        "prev_day": (target - timedelta(days=1)).isoformat(),
        "next_day": (target + timedelta(days=1)).isoformat(),
        "today": date.today().isoformat(),
        "org_name": ORGANISATION_NAME, "duty_station": DUTY_STATION, "tz_label": TIMEZONE_LABEL,
    })


@router.get("/public/calendar.ics", name="public_calendar_feed")
def public_calendar_feed(db: Session = Depends(get_db)):
    window_start = datetime.combine(date.today() - timedelta(days=FEED_PAST_DAYS), datetime.min.time())
    window_end = datetime.combine(date.today() + timedelta(days=FEED_FUTURE_DAYS), datetime.min.time())
    stmt = select(Booking).where(
        and_(Booking.starts_at < window_end, Booking.ends_at > window_start,
             Booking.status.in_(ACTIVE_BOOKING_STATUSES))
    )
    bookings = list(db.scalars(stmt))
    return Response(
        content=availability_feed_ics(bookings),
        media_type="text/calendar",
        headers={"Content-Disposition": 'inline; filename="room-availability.ics"'},
    )
