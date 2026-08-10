"""Cost recovery engine.

Charges are built in four layers:

  1. Room charge   - the cheapest of hourly / half-day / full-day for the booked duration.
  2. Service charge- add-on catalogue items (catering, interpretation, AV, security...).
  3. Rate card     - a multiplier per counterpart type plus a management (admin) fee.
                     UNDP projects recover at direct cost; sister UN agencies pay the
                     UN-to-UN rate plus a management fee; external bodies pay commercial.
  4. Waiver        - an approved percentage reduction, applied last, with justification.

Every booking stores the resulting figures as a snapshot so that later rate-card changes
never rewrite historical charges or issued invoices.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import OrgType, RateCard, Room

HALF_DAY_HOURS = 4.0
FULL_DAY_HOURS = 8.0

DEFAULT_RATE_CARDS = {
    OrgType.UNDP_PROJECT: dict(
        room_multiplier=1.0, service_multiplier=1.0, admin_fee_percent=0.0,
        notes="Internal UNDP projects are charged at direct cost only. No management fee "
              "is levied; the charge is a cost transfer between UNDP cost centres.",
    ),
    OrgType.UN_AGENCY: dict(
        room_multiplier=1.0, service_multiplier=1.0, admin_fee_percent=3.0,
        notes="UN-to-UN cost recovery at direct cost plus a 3% management fee covering "
              "administration of the common premises service.",
    ),
    OrgType.GOVERNMENT: dict(
        room_multiplier=1.15, service_multiplier=1.0, admin_fee_percent=5.0,
        notes="Government counterparts: direct cost plus overhead recovery.",
    ),
    OrgType.EXTERNAL: dict(
        room_multiplier=1.5, service_multiplier=1.15, admin_fee_percent=8.0,
        notes="Non-UN external users pay the full commercial rate including overhead.",
    ),
}


@dataclass
class ChargeLine:
    label: str
    detail: str
    amount: float


@dataclass
class Quote:
    duration_hours: float
    rate_basis: str
    room_charge: float
    services_charge: float
    admin_fee: float
    waiver_percent: float
    waiver_amount: float
    total: float
    lines: List[ChargeLine] = field(default_factory=list)
    service_lines: List[Dict] = field(default_factory=list)


def ensure_rate_cards(db: Session):
    for org_type, values in DEFAULT_RATE_CARDS.items():
        existing = db.scalar(select(RateCard).where(RateCard.org_type == org_type))
        if existing is None:
            db.add(RateCard(org_type=org_type, **values))
    db.commit()


def get_rate_card(db: Session, org_type: OrgType) -> RateCard:
    card = db.scalar(select(RateCard).where(RateCard.org_type == org_type))
    if card is None:
        card = RateCard(org_type=org_type, **DEFAULT_RATE_CARDS[org_type])
        db.add(card)
        db.commit()
    return card


def room_charge_for(room: Room, hours: float):
    """Return (amount, basis, human-readable detail) using the cheapest applicable tier."""
    hours = max(hours, 0.0)
    options = []

    if room.rate_hourly > 0:
        billed_hours = max(1.0, _round_up_half(hours))
        options.append((billed_hours * room.rate_hourly, "hourly",
                        "%.1f h billed @ %.2f/h" % (billed_hours, room.rate_hourly)))
    if room.rate_half_day > 0:
        blocks = max(1, _ceil(hours / HALF_DAY_HOURS))
        options.append((blocks * room.rate_half_day, "half_day",
                        "%d half-day block(s) @ %.2f" % (blocks, room.rate_half_day)))
    if room.rate_full_day > 0:
        days = max(1, _ceil(hours / FULL_DAY_HOURS))
        options.append((days * room.rate_full_day, "full_day",
                        "%d full day(s) @ %.2f" % (days, room.rate_full_day)))

    if not options:
        return 0.0, "hourly", "No rate configured for this room"
    return min(options, key=lambda o: o[0])


def _ceil(value: float) -> int:
    import math
    return int(math.ceil(value - 1e-9))


def _round_up_half(hours: float) -> float:
    """Bill in 30-minute increments."""
    import math
    return math.ceil((hours - 1e-9) * 2) / 2


def quote(db: Session, room: Room, org_type: OrgType, hours: float,
          services: Optional[List[Dict]] = None, waiver_percent: float = 0.0) -> Quote:
    """services: list of dicts {item: ServiceItem, quantity: float}"""
    card = get_rate_card(db, org_type)
    services = services or []

    base_room, basis, basis_detail = room_charge_for(room, hours)
    room_amount = round(base_room * card.room_multiplier, 2)

    lines = [ChargeLine(
        label="Room — %s" % room.name,
        detail="%s%s" % (basis_detail,
                         "" if card.room_multiplier == 1.0
                         else " × %.2f %s rate" % (card.room_multiplier, card.type_label)),
        amount=room_amount,
    )]

    service_total = 0.0
    service_lines = []
    for entry in services:
        item = entry["item"]
        qty = float(entry["quantity"])
        if qty <= 0:
            continue
        unit_price = round(item.unit_price * card.service_multiplier, 2)
        line_total = round(unit_price * qty, 2)
        service_total += line_total
        service_lines.append({
            "item": item, "quantity": qty, "unit_price": unit_price, "line_total": line_total,
        })
        lines.append(ChargeLine(
            label="%s — %s" % (item.category, item.name),
            detail="%g %s @ %.2f" % (qty, item.unit, unit_price),
            amount=line_total,
        ))

    service_total = round(service_total, 2)
    subtotal = round(room_amount + service_total, 2)
    admin_fee = round(subtotal * card.admin_fee_percent / 100.0, 2)
    if admin_fee:
        lines.append(ChargeLine(
            label="Management fee",
            detail="%.1f%% of direct cost (%s cost-recovery policy)"
                   % (card.admin_fee_percent, card.type_label),
            amount=admin_fee,
        ))

    gross = round(subtotal + admin_fee, 2)
    waiver_percent = max(0.0, min(100.0, float(waiver_percent or 0.0)))
    waiver_amount = round(gross * waiver_percent / 100.0, 2)
    if waiver_amount:
        lines.append(ChargeLine(
            label="Approved waiver",
            detail="%.1f%% reduction" % waiver_percent,
            amount=-waiver_amount,
        ))

    return Quote(
        duration_hours=hours,
        rate_basis=basis,
        room_charge=room_amount,
        services_charge=service_total,
        admin_fee=admin_fee,
        waiver_percent=waiver_percent,
        waiver_amount=waiver_amount,
        total=round(gross - waiver_amount, 2),
        lines=lines,
        service_lines=service_lines,
    )
