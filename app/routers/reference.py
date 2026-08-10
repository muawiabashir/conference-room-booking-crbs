"""Rooms, service catalogue, counterparts and rate cards."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ORG_TYPE_LABELS, OrgType, Organization, RateCard, Room, ServiceItem
from ..security import audit, current_user, has_permission, require
from ..templating import flash, render

router = APIRouter()


# ---------------------------------------------------------------- Rooms
@router.get("/rooms")
def rooms(request: Request, db: Session = Depends(get_db), user=Depends(current_user)):
    return render(request, db, user, "rooms.html", "rooms",
                  rooms=list(db.scalars(select(Room).order_by(Room.name))))


@router.post("/rooms")
def save_room(request: Request, code: str = Form(...), name: str = Form(...),
              location: str = Form(""), capacity: int = Form(...), features: str = Form(""),
              rate_hourly: float = Form(0), rate_half_day: float = Form(0),
              rate_full_day: float = Form(0), room_id: str = Form(""),
              db: Session = Depends(get_db), user=Depends(require("room.manage"))):
    room = db.get(Room, int(room_id)) if room_id else None
    creating = room is None
    if creating:
        if db.scalar(select(Room).where(Room.code == code.strip())):
            flash(request, "error", "Room code '%s' is already in use." % code.strip())
            return RedirectResponse("/rooms", status_code=303)
        room = Room(code=code.strip())
        db.add(room)

    room.name = name.strip()
    room.location = location.strip()
    room.capacity = max(1, capacity)
    room.features = features.strip()
    room.rate_hourly = max(0.0, rate_hourly)
    room.rate_half_day = max(0.0, rate_half_day)
    room.rate_full_day = max(0.0, rate_full_day)
    db.commit()

    audit(db, request, user, "ROOM_CREATE" if creating else "ROOM_UPDATE", "Room", room.code,
          "%s | cap %d | %.2f/h, %.2f/half-day, %.2f/day"
          % (room.name, room.capacity, room.rate_hourly, room.rate_half_day, room.rate_full_day))
    flash(request, "success", "Room %s saved." % room.name)
    return RedirectResponse("/rooms", status_code=303)


@router.post("/rooms/{room_id}/toggle")
def toggle_room(room_id: int, request: Request, db: Session = Depends(get_db),
                user=Depends(require("room.manage"))):
    room = db.get(Room, room_id)
    if room is None:
        raise HTTPException(404, "Room not found.")
    room.is_active = not room.is_active
    db.commit()
    audit(db, request, user, "ROOM_TOGGLE", "Room", room.code,
          "Set %s" % ("active" if room.is_active else "out of service"))
    flash(request, "success", "%s is now %s."
          % (room.name, "bookable" if room.is_active else "out of service"))
    return RedirectResponse("/rooms", status_code=303)


# ---------------------------------------------------------------- Services
@router.get("/services")
def services(request: Request, db: Session = Depends(get_db), user=Depends(current_user)):
    items = list(db.scalars(select(ServiceItem).order_by(ServiceItem.category, ServiceItem.name)))
    grouped = {}
    for item in items:
        grouped.setdefault(item.category, []).append(item)
    return render(request, db, user, "services.html", "services", grouped=grouped)


@router.post("/services")
def save_service(request: Request, code: str = Form(...), name: str = Form(...),
                 category: str = Form("General"), unit: str = Form("unit"),
                 unit_price: float = Form(0), service_id: str = Form(""),
                 db: Session = Depends(get_db), user=Depends(require("service.manage"))):
    item = db.get(ServiceItem, int(service_id)) if service_id else None
    creating = item is None
    if creating:
        if db.scalar(select(ServiceItem).where(ServiceItem.code == code.strip())):
            flash(request, "error", "Service code '%s' is already in use." % code.strip())
            return RedirectResponse("/services", status_code=303)
        item = ServiceItem(code=code.strip())
        db.add(item)

    item.name = name.strip()
    item.category = category.strip() or "General"
    item.unit = unit.strip() or "unit"
    item.unit_price = max(0.0, unit_price)
    db.commit()
    audit(db, request, user, "SERVICE_CREATE" if creating else "SERVICE_UPDATE",
          "ServiceItem", item.code, "%s @ %.2f per %s" % (item.name, item.unit_price, item.unit))
    flash(request, "success", "Service '%s' saved." % item.name)
    return RedirectResponse("/services", status_code=303)


# ---------------------------------------------------------------- Counterparts
@router.get("/organizations")
def organizations(request: Request, db: Session = Depends(get_db),
                  user=Depends(require("org.view"))):
    orgs = list(db.scalars(select(Organization).order_by(Organization.org_type, Organization.name)))
    grouped = {}
    for org in orgs:
        grouped.setdefault(org.type_label, []).append(org)
    return render(request, db, user, "organizations.html", "organizations",
                  grouped=grouped, org_types=list(OrgType), type_labels=ORG_TYPE_LABELS)


@router.post("/organizations")
def save_organization(request: Request, code: str = Form(...), name: str = Form(...),
                      org_type: str = Form(...), project_number: str = Form(""),
                      output_id: str = Form(""), donor: str = Form(""),
                      focal_point_name: str = Form(""), focal_point_email: str = Form(""),
                      billing_address: str = Form(""), org_id: str = Form(""),
                      db: Session = Depends(get_db), user=Depends(require("org.manage"))):
    org = db.get(Organization, int(org_id)) if org_id else None
    creating = org is None
    if creating:
        if db.scalar(select(Organization).where(Organization.code == code.strip().upper())):
            flash(request, "error", "Counterpart code '%s' already exists." % code.strip().upper())
            return RedirectResponse("/organizations", status_code=303)
        org = Organization(code=code.strip().upper())
        db.add(org)

    org.name = name.strip()
    org.org_type = OrgType(org_type)
    org.project_number = project_number.strip() or None
    org.output_id = output_id.strip() or None
    org.donor = donor.strip() or None
    org.focal_point_name = focal_point_name.strip() or None
    org.focal_point_email = focal_point_email.strip().lower() or None
    org.billing_address = billing_address.strip() or None

    if org.org_type == OrgType.UNDP_PROJECT and not org.project_number:
        flash(request, "error", "A UNDP project requires a project (award) number for charging.")
        db.rollback()
        return RedirectResponse("/organizations", status_code=303)

    db.commit()
    audit(db, request, user, "ORG_CREATE" if creating else "ORG_UPDATE",
          "Organization", org.code, "%s (%s) charge ref %s"
          % (org.name, org.type_label, org.charge_reference))
    flash(request, "success", "Counterpart %s saved." % org.name)
    return RedirectResponse("/organizations", status_code=303)


# ---------------------------------------------------------------- Rate cards
@router.get("/rate-cards")
def rate_cards(request: Request, db: Session = Depends(get_db),
               user=Depends(require("ratecard.manage"))):
    cards = list(db.scalars(select(RateCard).order_by(RateCard.org_type)))
    return render(request, db, user, "rate_cards.html", "ratecards", cards=cards)


@router.post("/rate-cards/{card_id}")
def save_rate_card(card_id: int, request: Request, room_multiplier: float = Form(...),
                   service_multiplier: float = Form(...), admin_fee_percent: float = Form(...),
                   notes: str = Form(""), db: Session = Depends(get_db),
                   user=Depends(require("ratecard.manage"))):
    card = db.get(RateCard, card_id)
    if card is None:
        raise HTTPException(404, "Rate card not found.")
    before = "x%.2f room, x%.2f services, %.1f%% fee" % (
        card.room_multiplier, card.service_multiplier, card.admin_fee_percent)

    card.room_multiplier = max(0.0, room_multiplier)
    card.service_multiplier = max(0.0, service_multiplier)
    card.admin_fee_percent = max(0.0, min(100.0, admin_fee_percent))
    card.notes = notes.strip()
    db.commit()

    audit(db, request, user, "RATECARD_UPDATE", "RateCard", card.org_type.value,
          "%s -> x%.2f room, x%.2f services, %.1f%% fee"
          % (before, card.room_multiplier, card.service_multiplier, card.admin_fee_percent))
    flash(request, "success",
          "Rate card for %s updated. Existing bookings keep their original charges." % card.type_label)
    return RedirectResponse("/rate-cards", status_code=303)
