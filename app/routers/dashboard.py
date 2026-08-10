from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    ACTIVE_BOOKING_STATUSES, Booking, BookingStatus, Invoice, InvoiceStatus, OrgType,
    Organization, Room, utcnow,
)
from ..security import current_user, has_permission, visible_org_ids
from ..sqlutil import duration_hours
from ..templating import render

router = APIRouter()

DAY_START_HOUR = 8
DAY_END_HOUR = 19


def _scope(stmt, user):
    scope = visible_org_ids(user)
    if scope is not None:
        stmt = stmt.where(Booking.organization_id.in_(scope or {-1}))
    return stmt


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db), user=Depends(current_user)):
    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def count(*conditions):
        return db.scalar(_scope(select(func.count()).select_from(Booking).where(*conditions), user)) or 0

    def total(*conditions):
        return db.scalar(_scope(select(func.sum(Booking.total_charge)).where(*conditions), user)) or 0.0

    pending = count(Booking.status == BookingStatus.PENDING)
    upcoming = count(Booking.status == BookingStatus.APPROVED, Booking.starts_at >= now)
    month_recovered = total(
        Booking.status.in_((BookingStatus.APPROVED, BookingStatus.COMPLETED)),
        Booking.starts_at >= month_start,
    )
    unbilled = total(
        Booking.status.in_((BookingStatus.APPROVED, BookingStatus.COMPLETED)),
        Booking.invoice_id.is_(None),
    )

    # Recovery split by counterpart type, current month
    split_stmt = (
        select(Organization.org_type, func.sum(Booking.total_charge), func.count(Booking.id))
        .join(Organization, Organization.id == Booking.organization_id)
        .where(
            Booking.status.in_((BookingStatus.APPROVED, BookingStatus.COMPLETED)),
            Booking.starts_at >= month_start,
        )
        .group_by(Organization.org_type)
    )
    split_rows = list(db.execute(_scope(split_stmt, user)))
    split_max = max([r[1] or 0 for r in split_rows], default=0) or 1

    # Room utilisation over the last 30 days (booked hours vs available hours)
    since = now - timedelta(days=30)
    rooms = list(db.scalars(select(Room).where(Room.is_active.is_(True)).order_by(Room.name)))
    available_hours = 30 * (DAY_END_HOUR - DAY_START_HOUR) * (5 / 7.0)
    utilisation = []
    for room in rooms:
        booked = db.scalar(
            select(func.sum(duration_hours(Booking.starts_at, Booking.ends_at)))
            .where(
                Booking.room_id == room.id,
                Booking.status.in_(ACTIVE_BOOKING_STATUSES),
                Booking.starts_at >= since,
            )
        ) or 0
        hours = float(booked)
        utilisation.append({
            "room": room,
            "hours": round(hours, 1),
            "percent": min(100, round(hours / available_hours * 100)) if available_hours else 0,
        })
    utilisation.sort(key=lambda u: -u["percent"])

    upcoming_stmt = (
        select(Booking)
        .where(Booking.ends_at >= now, Booking.status.in_(ACTIVE_BOOKING_STATUSES))
        .order_by(Booking.starts_at).limit(8)
    )
    upcoming_list = list(db.scalars(_scope(upcoming_stmt, user)))

    approvals = []
    if has_permission(user, "booking.approve"):
        approvals = list(db.scalars(
            select(Booking).where(Booking.status == BookingStatus.PENDING)
            .order_by(Booking.starts_at).limit(6)
        ))

    invoice_summary = None
    if has_permission(user, "invoice.view.all"):
        invoice_summary = {
            "draft": db.scalar(select(func.count()).select_from(Invoice)
                               .where(Invoice.status == InvoiceStatus.DRAFT)) or 0,
            "issued": db.scalar(select(func.sum(Invoice.total))
                                .where(Invoice.status == InvoiceStatus.ISSUED)) or 0.0,
            "paid": db.scalar(select(func.sum(Invoice.total))
                              .where(Invoice.status == InvoiceStatus.PAID)) or 0.0,
        }

    return render(request, db, user, "dashboard.html", "dashboard",
                  pending=pending, upcoming_count=upcoming, month_recovered=month_recovered,
                  unbilled=unbilled, split_rows=split_rows, split_max=split_max,
                  utilisation=utilisation, upcoming_list=upcoming_list, approvals=approvals,
                  invoice_summary=invoice_summary, month_label=now.strftime("%B %Y"))


@router.get("/calendar")
def calendar(request: Request, day: str = "", db: Session = Depends(get_db),
             user=Depends(current_user)):
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
    ).order_by(Booking.starts_at)
    bookings = list(db.scalars(_scope(stmt, user)))

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
            "booking": booking,
            "top_pct": offset / total_minutes * 100,
            "height_pct": max(3.0, length / total_minutes * 100),
        })

    return render(request, db, user, "calendar.html", "calendar",
                  target=target, rooms=rooms, hours=hours, events=events,
                  prev_day=(target - timedelta(days=1)).isoformat(),
                  next_day=(target + timedelta(days=1)).isoformat(),
                  today=date.today().isoformat(), booking_count=len(bookings))
