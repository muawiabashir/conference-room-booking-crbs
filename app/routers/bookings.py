from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import CALENDAR_SYNC_ENABLED
from ..database import get_db
from .. import graph
from ..ics import booking_ics
from ..models import (
    Booking, BookingService, BookingStatus, Organization, Room, ServiceItem, utcnow,
)
from ..pricing import quote
from ..security import (
    assert_can_touch_org, audit, current_user, has_permission, require, visible_org_ids,
)
from ..templating import flash, render
from ..workflow import find_conflicts, next_reference, validate_window

router = APIRouter()


def _parse_dt(raw: str):
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except (ValueError, TypeError):
        return None


def _selectable_orgs(db: Session, user):
    scope = visible_org_ids(user)
    stmt = select(Organization).where(Organization.is_active.is_(True)).order_by(Organization.name)
    if scope is not None:
        stmt = stmt.where(Organization.id.in_(scope or {-1}))
    return list(db.scalars(stmt))


def _get_booking(db: Session, user, booking_id: int) -> Booking:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found.")
    scope = visible_org_ids(user)
    if scope is not None and booking.organization_id not in scope:
        raise HTTPException(403, "This booking belongs to another counterpart.")
    return booking


@router.get("/bookings")
def list_bookings(request: Request, status: str = "", org: str = "", q: str = "",
                  db: Session = Depends(get_db), user=Depends(require("booking.view.own"))):
    stmt = select(Booking).order_by(Booking.starts_at.desc())
    scope = visible_org_ids(user)
    if scope is not None:
        stmt = stmt.where(Booking.organization_id.in_(scope or {-1}))
    if status:
        stmt = stmt.where(Booking.status == BookingStatus(status))
    if org:
        stmt = stmt.where(Booking.organization_id == int(org))
    if q:
        like = "%%%s%%" % q
        stmt = stmt.where(Booking.title.ilike(like) | Booking.reference.ilike(like))

    bookings = list(db.scalars(stmt.limit(300)))
    return render(request, db, user, "bookings_list.html", "bookings",
                  bookings=bookings, statuses=list(BookingStatus),
                  orgs=_selectable_orgs(db, user),
                  f_status=status, f_org=org, f_q=q)


@router.get("/bookings/new")
def new_booking(request: Request, db: Session = Depends(get_db),
                user=Depends(require("booking.create"))):
    return render(request, db, user, "booking_form.html", "bookings",
                  booking=None, errors=[], form={},
                  rooms=list(db.scalars(select(Room).where(Room.is_active.is_(True)).order_by(Room.name))),
                  orgs=_selectable_orgs(db, user),
                  services=list(db.scalars(select(ServiceItem).where(ServiceItem.is_active.is_(True))
                                           .order_by(ServiceItem.category, ServiceItem.name))),
                  selected_services={}, preview=None)


@router.post("/bookings/new")
async def create_booking(request: Request, db: Session = Depends(get_db),
                         user=Depends(require("booking.create"))):
    form = await request.form()
    return _save_booking(request, db, user, form, booking=None)


@router.get("/bookings/{booking_id}")
def booking_detail(booking_id: int, request: Request, db: Session = Depends(get_db),
                   user=Depends(require("booking.view.own"))):
    booking = _get_booking(db, user, booking_id)
    breakdown = quote(
        db, booking.room, booking.organization.org_type, booking.duration_hours,
        [{"item": s.service_item, "quantity": s.quantity} for s in booking.services],
        booking.waiver_percent,
    )
    return render(request, db, user, "booking_detail.html", "bookings",
                  booking=booking, breakdown=breakdown)


@router.get("/bookings/{booking_id}/ics")
def booking_ics_download(booking_id: int, db: Session = Depends(get_db),
                         user=Depends(require("booking.view.own"))):
    booking = _get_booking(db, user, booking_id)
    if booking.status in (BookingStatus.CANCELLED, BookingStatus.REJECTED):
        raise HTTPException(400, "This booking is closed and can no longer be added to a calendar.")
    return Response(
        content=booking_ics(booking),
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="%s.ics"' % booking.reference},
    )


@router.get("/bookings/{booking_id}/edit")
def edit_booking(booking_id: int, request: Request, db: Session = Depends(get_db),
                 user=Depends(require("booking.edit"))):
    booking = _get_booking(db, user, booking_id)
    if booking.status not in (BookingStatus.DRAFT, BookingStatus.PENDING):
        flash(request, "warn", "Only draft or pending bookings can be amended.")
        return RedirectResponse("/bookings/%d" % booking_id, status_code=303)
    return render(request, db, user, "booking_form.html", "bookings",
                  booking=booking, errors=[], form={},
                  rooms=list(db.scalars(select(Room).where(Room.is_active.is_(True)).order_by(Room.name))),
                  orgs=_selectable_orgs(db, user),
                  services=list(db.scalars(select(ServiceItem).where(ServiceItem.is_active.is_(True))
                                           .order_by(ServiceItem.category, ServiceItem.name))),
                  selected_services={s.service_item_id: s.quantity for s in booking.services},
                  preview=None)


@router.post("/bookings/{booking_id}/edit")
async def update_booking(booking_id: int, request: Request, db: Session = Depends(get_db),
                         user=Depends(require("booking.edit"))):
    booking = _get_booking(db, user, booking_id)
    if booking.status not in (BookingStatus.DRAFT, BookingStatus.PENDING):
        raise HTTPException(403, "Only draft or pending bookings can be amended.")
    form = await request.form()
    return _save_booking(request, db, user, form, booking=booking)


def _save_booking(request: Request, db: Session, user, form, booking):
    errors = []
    title = (form.get("title") or "").strip()
    purpose = (form.get("purpose") or "").strip()
    room_id = form.get("room_id")
    org_id = form.get("organization_id")
    starts_at = _parse_dt(form.get("starts_at"))
    ends_at = _parse_dt(form.get("ends_at"))
    try:
        attendees = int(form.get("attendees") or 1)
    except ValueError:
        attendees = 0

    if not title:
        errors.append("A meeting title is required.")
    if not room_id:
        errors.append("Select a room.")
    if not org_id:
        errors.append("Select the counterpart to be charged.")
    if starts_at is None or ends_at is None:
        errors.append("Provide a valid start and end date/time.")
    if attendees < 1:
        errors.append("Number of attendees must be at least 1.")

    room = db.get(Room, int(room_id)) if room_id else None
    organization = db.get(Organization, int(org_id)) if org_id else None
    if org_id:
        assert_can_touch_org(user, int(org_id))

    if starts_at and ends_at:
        errors.extend(validate_window(starts_at, ends_at))
    if room and attendees > room.capacity:
        errors.append("%s seats %d; you entered %d attendees."
                      % (room.name, room.capacity, attendees))
    if room and starts_at and ends_at and not errors:
        conflicts = find_conflicts(db, room.id, starts_at, ends_at,
                                   exclude_booking_id=booking.id if booking else None)
        if conflicts:
            c = conflicts[0]
            errors.append(
                "%s is already booked %s–%s (%s). A %d-minute set-up gap is also enforced."
                % (room.name, c.starts_at.strftime("%H:%M"), c.ends_at.strftime("%H:%M"),
                   c.reference, 15)
            )

    # Chosen add-on services
    chosen = []
    selected_services = {}
    for key, value in form.multi_items():
        if not key.startswith("svc_qty_"):
            continue
        try:
            qty = float(value or 0)
        except ValueError:
            qty = 0
        if qty <= 0:
            continue
        item = db.get(ServiceItem, int(key[len("svc_qty_"):]))
        if item and item.is_active:
            chosen.append({"item": item, "quantity": qty})
            selected_services[item.id] = qty

    waiver_percent = 0.0
    if booking is not None:
        waiver_percent = booking.waiver_percent

    if errors:
        return render(request, db, user, "booking_form.html", "bookings",
                      booking=booking, errors=errors, form=dict(form),
                      rooms=list(db.scalars(select(Room).where(Room.is_active.is_(True)).order_by(Room.name))),
                      orgs=_selectable_orgs(db, user),
                      services=list(db.scalars(select(ServiceItem).where(ServiceItem.is_active.is_(True))
                                               .order_by(ServiceItem.category, ServiceItem.name))),
                      selected_services=selected_services, preview=None)

    hours = (ends_at - starts_at).total_seconds() / 3600.0
    q = quote(db, room, organization.org_type, hours, chosen, waiver_percent)

    creating = booking is None
    if creating:
        booking = Booking(reference=next_reference(db), requester_id=user.id,
                          status=BookingStatus.PENDING)
        db.add(booking)

    booking.title = title
    booking.purpose = purpose
    booking.room_id = room.id
    booking.organization_id = organization.id
    booking.starts_at = starts_at
    booking.ends_at = ends_at
    booking.attendees = attendees
    booking.rate_basis = q.rate_basis
    booking.room_charge = q.room_charge
    booking.services_charge = q.services_charge
    booking.admin_fee = q.admin_fee
    booking.waiver_amount = q.waiver_amount
    booking.total_charge = q.total

    booking.services.clear()
    db.flush()
    for line in q.service_lines:
        booking.services.append(BookingService(
            service_item_id=line["item"].id, quantity=line["quantity"],
            unit_price=line["unit_price"], line_total=line["line_total"],
        ))
    db.commit()

    audit(db, request, user, "BOOKING_CREATE" if creating else "BOOKING_UPDATE",
          "Booking", booking.reference,
          "%s | %s | %s to %s | charge %.2f"
          % (title, room.name, starts_at, ends_at, booking.total_charge))
    flash(request, "success",
          "Booking %s %s and awaiting approval. Estimated cost recovery: %.2f."
          % (booking.reference, "created" if creating else "updated", booking.total_charge))
    return RedirectResponse("/bookings/%d" % booking.id, status_code=303)


@router.post("/bookings/{booking_id}/decision")
def decide(booking_id: int, request: Request, decision: str = Form(...),
           note: str = Form(""), db: Session = Depends(get_db),
           user=Depends(require("booking.approve"))):
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found.")
    if booking.status != BookingStatus.PENDING:
        flash(request, "warn", "Only pending bookings can be decided.")
        return RedirectResponse("/bookings/%d" % booking_id, status_code=303)

    if decision == "approve":
        conflicts = find_conflicts(db, booking.room_id, booking.starts_at, booking.ends_at,
                                   exclude_booking_id=booking.id)
        approved_conflicts = [c for c in conflicts if c.status == BookingStatus.APPROVED]
        if approved_conflicts:
            flash(request, "error",
                  "Cannot approve: %s now clashes with approved booking %s."
                  % (booking.reference, approved_conflicts[0].reference))
            return RedirectResponse("/bookings/%d" % booking_id, status_code=303)
        booking.status = BookingStatus.APPROVED
    elif decision == "reject":
        booking.status = BookingStatus.REJECTED
    else:
        raise HTTPException(400, "Unknown decision.")

    booking.decided_by_id = user.id
    booking.decided_at = utcnow()
    booking.decision_note = note
    db.commit()

    audit(db, request, user, "BOOKING_%s" % booking.status.value, "Booking", booking.reference, note)
    message = "Booking %s has been %s." % (booking.reference, booking.status.value.lower())
    if booking.status == BookingStatus.APPROVED and CALENDAR_SYNC_ENABLED:
        event_id = graph.create_event(booking)
        if event_id:
            booking.graph_event_id = event_id
            db.commit()
        else:
            message += " Calendar sync failed — check server logs."
    flash(request, "success", message)
    return RedirectResponse("/bookings/%d" % booking_id, status_code=303)


@router.post("/bookings/{booking_id}/cancel")
def cancel(booking_id: int, request: Request, reason: str = Form(""),
           db: Session = Depends(get_db), user=Depends(require("booking.cancel"))):
    booking = _get_booking(db, user, booking_id)
    if booking.invoice_id:
        flash(request, "error", "This booking has been invoiced and can no longer be cancelled.")
        return RedirectResponse("/bookings/%d" % booking_id, status_code=303)
    if booking.status in (BookingStatus.CANCELLED, BookingStatus.REJECTED):
        flash(request, "warn", "Booking is already closed.")
        return RedirectResponse("/bookings/%d" % booking_id, status_code=303)

    booking.status = BookingStatus.CANCELLED
    booking.decision_note = reason
    booking.total_charge = 0.0
    booking.room_charge = 0.0
    booking.services_charge = 0.0
    booking.admin_fee = 0.0

    event_id, booking.graph_event_id = booking.graph_event_id, None
    db.commit()
    audit(db, request, user, "BOOKING_CANCELLED", "Booking", booking.reference, reason)
    message = "Booking %s cancelled; charges reversed." % booking.reference
    if event_id and not graph.delete_event(event_id):
        message += " Calendar sync failed — check server logs."
    flash(request, "success", message)
    return RedirectResponse("/bookings/%d" % booking_id, status_code=303)


@router.post("/bookings/{booking_id}/waiver")
def apply_waiver(booking_id: int, request: Request, percent: float = Form(...),
                 justification: str = Form(...), db: Session = Depends(get_db),
                 user=Depends(require("booking.waive"))):
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found.")
    if booking.invoice_id:
        flash(request, "error", "Cannot waive charges on an invoiced booking.")
        return RedirectResponse("/bookings/%d" % booking_id, status_code=303)
    if not justification.strip():
        flash(request, "error", "A written justification is mandatory for any waiver.")
        return RedirectResponse("/bookings/%d" % booking_id, status_code=303)

    q = quote(db, booking.room, booking.organization.org_type, booking.duration_hours,
              [{"item": s.service_item, "quantity": s.quantity} for s in booking.services],
              percent)
    booking.waiver_percent = q.waiver_percent
    booking.waiver_reason = justification.strip()
    booking.waiver_amount = q.waiver_amount
    booking.total_charge = q.total
    db.commit()

    audit(db, request, user, "WAIVER_APPLIED", "Booking", booking.reference,
          "%.1f%% (%.2f) — %s" % (q.waiver_percent, q.waiver_amount, justification.strip()))
    flash(request, "success", "Waiver of %.1f%% applied to %s." % (q.waiver_percent, booking.reference))
    return RedirectResponse("/bookings/%d" % booking_id, status_code=303)


@router.post("/api/quote")
async def api_quote(request: Request, db: Session = Depends(get_db),
                    user=Depends(require("booking.create"))):
    """Live cost estimate for the booking form."""
    form = await request.form()
    room = db.get(Room, int(form.get("room_id") or 0))
    organization = db.get(Organization, int(form.get("organization_id") or 0))
    starts_at = _parse_dt(form.get("starts_at"))
    ends_at = _parse_dt(form.get("ends_at"))
    if not (room and organization and starts_at and ends_at and ends_at > starts_at):
        return {"ok": False, "message": "Select a room, counterpart and a valid time range."}

    chosen = []
    for key, value in form.multi_items():
        if key.startswith("svc_qty_"):
            try:
                qty = float(value or 0)
            except ValueError:
                continue
            item = db.get(ServiceItem, int(key[len("svc_qty_"):]))
            if item and qty > 0:
                chosen.append({"item": item, "quantity": qty})

    hours = (ends_at - starts_at).total_seconds() / 3600.0
    q = quote(db, room, organization.org_type, hours, chosen)
    conflicts = find_conflicts(db, room.id, starts_at, ends_at,
                               exclude_booking_id=int(form.get("booking_id") or 0) or None)
    return {
        "ok": True,
        "hours": round(hours, 2),
        "basis": q.rate_basis.replace("_", " "),
        "policy": organization.type_label,
        "lines": [{"label": l.label, "detail": l.detail, "amount": l.amount} for l in q.lines],
        "total": q.total,
        "conflict": conflicts[0].reference if conflicts else None,
    }
