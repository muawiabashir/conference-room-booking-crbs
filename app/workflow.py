"""Booking availability, reference numbering and invoice assembly."""
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .config import ADVANCE_BOOKING_DAYS, BOOKING_BUFFER_MINUTES, MAX_BOOKING_HOURS
from .models import (
    ACTIVE_BOOKING_STATUSES, Booking, BookingStatus, Invoice, InvoiceStatus, Organization,
    Room, utcnow,
)


def next_reference(db: Session) -> str:
    year = utcnow().year
    prefix = "BKG-%d-" % year
    count = db.scalar(
        select(func.count()).select_from(Booking).where(Booking.reference.like(prefix + "%"))
    ) or 0
    return "%s%04d" % (prefix, count + 1)


def next_invoice_number(db: Session) -> str:
    year = utcnow().year
    prefix = "INV-%d-" % year
    count = db.scalar(
        select(func.count()).select_from(Invoice).where(Invoice.number.like(prefix + "%"))
    ) or 0
    return "%s%04d" % (prefix, count + 1)


def validate_window(starts_at: datetime, ends_at: datetime):
    errors = []
    if ends_at <= starts_at:
        errors.append("The end time must be after the start time.")
        return errors
    hours = (ends_at - starts_at).total_seconds() / 3600.0
    if hours > MAX_BOOKING_HOURS:
        errors.append("A single booking may not exceed %d hours." % MAX_BOOKING_HOURS)
    if starts_at > utcnow() + timedelta(days=ADVANCE_BOOKING_DAYS):
        errors.append("Bookings may not be made more than %d days ahead." % ADVANCE_BOOKING_DAYS)
    return errors


def find_conflicts(db: Session, room_id: int, starts_at: datetime, ends_at: datetime,
                   exclude_booking_id=None):
    """Overlap check including the set-up / tear-down buffer."""
    buffer = timedelta(minutes=BOOKING_BUFFER_MINUTES)
    window_start = starts_at - buffer
    window_end = ends_at + buffer
    stmt = select(Booking).where(
        and_(
            Booking.room_id == room_id,
            Booking.status.in_(ACTIVE_BOOKING_STATUSES),
            Booking.starts_at < window_end,
            Booking.ends_at > window_start,
        )
    )
    if exclude_booking_id:
        stmt = stmt.where(Booking.id != exclude_booking_id)
    return list(db.scalars(stmt))


def available_rooms(db: Session, starts_at: datetime, ends_at: datetime, min_capacity: int = 0):
    rooms = list(db.scalars(select(Room).where(Room.is_active.is_(True)).order_by(Room.name)))
    result = []
    for room in rooms:
        if min_capacity and room.capacity < min_capacity:
            continue
        conflicts = find_conflicts(db, room.id, starts_at, ends_at)
        result.append({"room": room, "available": not conflicts, "conflicts": conflicts})
    return result


def uninvoiced_bookings(db: Session, organization_id: int, period_start: datetime,
                        period_end: datetime):
    stmt = select(Booking).where(
        and_(
            Booking.organization_id == organization_id,
            Booking.invoice_id.is_(None),
            Booking.status.in_((BookingStatus.APPROVED, BookingStatus.COMPLETED)),
            Booking.starts_at >= period_start,
            Booking.starts_at < period_end,
            Booking.total_charge > 0,
        )
    ).order_by(Booking.starts_at)
    return list(db.scalars(stmt))


def build_invoice(db: Session, organization: Organization, period_start: datetime,
                  period_end: datetime, created_by):
    bookings = uninvoiced_bookings(db, organization.id, period_start, period_end)
    if not bookings:
        return None, "No uninvoiced billable bookings for %s in the selected period." % organization.name

    subtotal = round(sum(b.room_charge + b.services_charge for b in bookings), 2)
    admin_fee = round(sum(b.admin_fee for b in bookings), 2)
    total = round(sum(b.total_charge for b in bookings), 2)

    invoice = Invoice(
        number=next_invoice_number(db),
        organization_id=organization.id,
        period_start=period_start,
        period_end=period_end,
        status=InvoiceStatus.DRAFT,
        subtotal=subtotal,
        admin_fee=admin_fee,
        total=total,
        created_by_id=created_by.id,
    )
    db.add(invoice)
    db.flush()
    for booking in bookings:
        booking.invoice_id = invoice.id
    db.commit()
    return invoice, None
