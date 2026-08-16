from datetime import datetime, timezone
import enum

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import mapped_column, relationship

from .database import Base


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(str, enum.Enum):
    SYSTEM_ADMIN = "SYSTEM_ADMIN"
    FACILITY_MANAGER = "FACILITY_MANAGER"
    FINANCE_OFFICER = "FINANCE_OFFICER"
    AGENCY_FOCAL_POINT = "AGENCY_FOCAL_POINT"
    REQUESTER = "REQUESTER"
    AUDITOR = "AUDITOR"

    @property
    def label(self):
        return ROLE_LABELS[self.value]


ROLE_LABELS = {
    "SYSTEM_ADMIN": "System Administrator",
    "FACILITY_MANAGER": "Facility Manager",
    "FINANCE_OFFICER": "Finance Officer",
    "AGENCY_FOCAL_POINT": "Agency Focal Point",
    "REQUESTER": "Requester",
    "AUDITOR": "Auditor (read-only)",
}


class OrgType(str, enum.Enum):
    UNDP_PROJECT = "UNDP_PROJECT"        # internal UNDP project / cost centre
    UN_AGENCY = "UN_AGENCY"              # sister UN agency, UN-to-UN cost recovery
    GOVERNMENT = "GOVERNMENT"
    EXTERNAL = "EXTERNAL"                # NGO, private sector, donor


ORG_TYPE_LABELS = {
    "UNDP_PROJECT": "UNDP Project",
    "UN_AGENCY": "UN Agency",
    "GOVERNMENT": "Government",
    "EXTERNAL": "External / Other",
}


class BookingStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


ACTIVE_BOOKING_STATUSES = (BookingStatus.PENDING, BookingStatus.APPROVED, BookingStatus.COMPLETED)


class InvoiceStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    PAID = "PAID"
    VOID = "VOID"


class User(Base):
    __tablename__ = "users"

    id = mapped_column(Integer, primary_key=True)
    email = mapped_column(String(180), unique=True, nullable=False, index=True)
    full_name = mapped_column(String(160), nullable=False)
    password_hash = mapped_column(String(255), nullable=False)
    # Microsoft Entra ID "oid" claim, bound on first successful SSO sign-in.
    # Preferred over email for matching on later logins so a mailbox reassigned
    # to someone else in the tenant can't inherit the old owner's account.
    sso_subject = mapped_column(String(160), unique=True, nullable=True, index=True)
    role = mapped_column(Enum(Role), nullable=False, default=Role.REQUESTER)
    organization_id = mapped_column(ForeignKey("organizations.id"), nullable=True)
    is_active = mapped_column(Boolean, nullable=False, default=True)
    must_change_password = mapped_column(Boolean, nullable=False, default=False)
    failed_logins = mapped_column(Integer, nullable=False, default=0)
    locked_until = mapped_column(DateTime, nullable=True)
    last_login_at = mapped_column(DateTime, nullable=True)
    created_at = mapped_column(DateTime, nullable=False, default=utcnow)

    organization = relationship("Organization", lazy="joined")

    @property
    def initials(self):
        parts = [p for p in self.full_name.split() if p]
        return "".join(p[0].upper() for p in parts[:2]) or "?"


class Organization(Base):
    """A billable counterpart: a UNDP project, a UN agency, government or external body."""
    __tablename__ = "organizations"

    id = mapped_column(Integer, primary_key=True)
    code = mapped_column(String(40), unique=True, nullable=False)
    name = mapped_column(String(200), nullable=False)
    org_type = mapped_column(Enum(OrgType), nullable=False)
    # UNDP project attributes (Quantum/Atlas coding block)
    project_number = mapped_column(String(40), nullable=True)
    output_id = mapped_column(String(40), nullable=True)
    donor = mapped_column(String(160), nullable=True)
    # UN agency attributes
    focal_point_name = mapped_column(String(160), nullable=True)
    focal_point_email = mapped_column(String(180), nullable=True)
    billing_address = mapped_column(Text, nullable=True)
    is_active = mapped_column(Boolean, nullable=False, default=True)
    created_at = mapped_column(DateTime, nullable=False, default=utcnow)

    @property
    def type_label(self):
        return ORG_TYPE_LABELS[self.org_type.value]

    @property
    def charge_reference(self):
        if self.org_type == OrgType.UNDP_PROJECT and self.project_number:
            return "%s / %s" % (self.project_number, self.output_id or "-")
        return self.code


class RateCard(Base):
    """Cost-recovery policy applied per counterpart type.

    UNDP projects are charged at direct cost (no margin, no GMS). Sister UN agencies are
    charged under UN-to-UN cost recovery with a management fee. External bodies pay the
    commercial multiplier.
    """
    __tablename__ = "rate_cards"

    id = mapped_column(Integer, primary_key=True)
    org_type = mapped_column(Enum(OrgType), unique=True, nullable=False)
    room_multiplier = mapped_column(Float, nullable=False, default=1.0)
    service_multiplier = mapped_column(Float, nullable=False, default=1.0)
    admin_fee_percent = mapped_column(Float, nullable=False, default=0.0)
    notes = mapped_column(Text, nullable=True)

    @property
    def type_label(self):
        return ORG_TYPE_LABELS[self.org_type.value]


class Room(Base):
    __tablename__ = "rooms"

    id = mapped_column(Integer, primary_key=True)
    code = mapped_column(String(30), unique=True, nullable=False)
    name = mapped_column(String(140), nullable=False)
    location = mapped_column(String(140), nullable=False, default="")
    capacity = mapped_column(Integer, nullable=False, default=10)
    features = mapped_column(String(400), nullable=False, default="")  # comma separated
    rate_hourly = mapped_column(Float, nullable=False, default=0.0)
    rate_half_day = mapped_column(Float, nullable=False, default=0.0)
    rate_full_day = mapped_column(Float, nullable=False, default=0.0)
    is_active = mapped_column(Boolean, nullable=False, default=True)

    @property
    def feature_list(self):
        return [f.strip() for f in self.features.split(",") if f.strip()]


class ServiceItem(Base):
    """Chargeable add-on: catering, interpretation, AV, security, cleaning."""
    __tablename__ = "service_items"

    id = mapped_column(Integer, primary_key=True)
    code = mapped_column(String(30), unique=True, nullable=False)
    name = mapped_column(String(160), nullable=False)
    category = mapped_column(String(60), nullable=False, default="General")
    unit = mapped_column(String(40), nullable=False, default="unit")
    unit_price = mapped_column(Float, nullable=False, default=0.0)
    is_active = mapped_column(Boolean, nullable=False, default=True)


class Booking(Base):
    __tablename__ = "bookings"

    id = mapped_column(Integer, primary_key=True)
    reference = mapped_column(String(30), unique=True, nullable=False, index=True)
    title = mapped_column(String(200), nullable=False)
    purpose = mapped_column(Text, nullable=False, default="")
    room_id = mapped_column(ForeignKey("rooms.id"), nullable=False)
    organization_id = mapped_column(ForeignKey("organizations.id"), nullable=False)
    requester_id = mapped_column(ForeignKey("users.id"), nullable=False)
    starts_at = mapped_column(DateTime, nullable=False, index=True)
    ends_at = mapped_column(DateTime, nullable=False)
    attendees = mapped_column(Integer, nullable=False, default=1)
    status = mapped_column(Enum(BookingStatus), nullable=False, default=BookingStatus.PENDING)

    waiver_percent = mapped_column(Float, nullable=False, default=0.0)
    waiver_reason = mapped_column(Text, nullable=True)

    # Priced snapshot, frozen at save time so later rate changes do not alter history.
    rate_basis = mapped_column(String(20), nullable=False, default="hourly")
    room_charge = mapped_column(Float, nullable=False, default=0.0)
    services_charge = mapped_column(Float, nullable=False, default=0.0)
    admin_fee = mapped_column(Float, nullable=False, default=0.0)
    waiver_amount = mapped_column(Float, nullable=False, default=0.0)
    total_charge = mapped_column(Float, nullable=False, default=0.0)

    decided_by_id = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at = mapped_column(DateTime, nullable=True)
    decision_note = mapped_column(Text, nullable=True)
    invoice_id = mapped_column(ForeignKey("invoices.id"), nullable=True)
    created_at = mapped_column(DateTime, nullable=False, default=utcnow)

    room = relationship("Room", lazy="joined")
    organization = relationship("Organization", lazy="joined")
    requester = relationship("User", foreign_keys=[requester_id], lazy="joined")
    decided_by = relationship("User", foreign_keys=[decided_by_id], lazy="joined")
    services = relationship(
        "BookingService", cascade="all, delete-orphan", back_populates="booking", lazy="selectin"
    )
    invoice = relationship("Invoice", back_populates="bookings", lazy="joined")

    @property
    def duration_hours(self):
        return (self.ends_at - self.starts_at).total_seconds() / 3600.0

    @property
    def is_billable(self):
        return self.status in (BookingStatus.APPROVED, BookingStatus.COMPLETED)


class BookingService(Base):
    __tablename__ = "booking_services"

    id = mapped_column(Integer, primary_key=True)
    booking_id = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False)
    service_item_id = mapped_column(ForeignKey("service_items.id"), nullable=False)
    quantity = mapped_column(Float, nullable=False, default=1.0)
    unit_price = mapped_column(Float, nullable=False, default=0.0)   # snapshot
    line_total = mapped_column(Float, nullable=False, default=0.0)

    booking = relationship("Booking", back_populates="services")
    service_item = relationship("ServiceItem", lazy="joined")


class Invoice(Base):
    __tablename__ = "invoices"

    id = mapped_column(Integer, primary_key=True)
    number = mapped_column(String(30), unique=True, nullable=False, index=True)
    organization_id = mapped_column(ForeignKey("organizations.id"), nullable=False)
    period_start = mapped_column(DateTime, nullable=False)
    period_end = mapped_column(DateTime, nullable=False)
    status = mapped_column(Enum(InvoiceStatus), nullable=False, default=InvoiceStatus.DRAFT)
    subtotal = mapped_column(Float, nullable=False, default=0.0)
    admin_fee = mapped_column(Float, nullable=False, default=0.0)
    total = mapped_column(Float, nullable=False, default=0.0)
    issued_at = mapped_column(DateTime, nullable=True)
    paid_at = mapped_column(DateTime, nullable=True)
    created_by_id = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at = mapped_column(DateTime, nullable=False, default=utcnow)

    organization = relationship("Organization", lazy="joined")
    created_by = relationship("User", lazy="joined")
    bookings = relationship("Booking", back_populates="invoice", lazy="selectin")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (UniqueConstraint("id"),)

    id = mapped_column(Integer, primary_key=True)
    at = mapped_column(DateTime, nullable=False, default=utcnow, index=True)
    actor_id = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_email = mapped_column(String(180), nullable=False, default="anonymous")
    action = mapped_column(String(60), nullable=False)
    entity_type = mapped_column(String(60), nullable=False, default="")
    entity_id = mapped_column(String(60), nullable=False, default="")
    detail = mapped_column(Text, nullable=False, default="")
    ip_address = mapped_column(String(60), nullable=False, default="")
    outcome = mapped_column(String(20), nullable=False, default="SUCCESS")
