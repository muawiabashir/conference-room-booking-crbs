"""Populate the database with a realistic demo dataset.

Usage:  .venv/bin/python seed.py [--reset]
"""
import random
import sys
from datetime import datetime, timedelta

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import (
    Booking, BookingService, BookingStatus, OrgType, Organization, Role, Room, ServiceItem, User,
)
from app.pricing import ensure_rate_cards, quote
from app.security import hash_password
from app.workflow import find_conflicts, next_reference

DEMO_PASSWORD = "Undp@2026!"

ROOMS = [
    ("CR-01", "Nile Conference Hall", "Main Building, Ground Floor", 120,
     "Interpretation booths, Dual projection, Podium, Hybrid VC", 85, 300, 520),
    ("CR-02", "Blue Nile Meeting Room", "Main Building, 1st Floor", 40,
     "Projector, Hybrid VC, Whiteboard", 45, 160, 280),
    ("CR-03", "Sahel Board Room", "Annex, 2nd Floor", 18,
     "Video conferencing, Smart display", 35, 120, 210),
    ("CR-04", "Training Room A", "Annex, Ground Floor", 30,
     "Flip charts, Projector, Modular seating", 28, 100, 170),
    ("CR-05", "Huddle Room", "Main Building, 1st Floor", 8,
     "Screen share, Speakerphone", 15, 55, 90),
]

SERVICES = [
    ("CAT-COFFEE", "Coffee break (per person)", "Catering", "person", 6.00),
    ("CAT-LUNCH", "Working lunch (per person)", "Catering", "person", 18.00),
    ("CAT-WATER", "Bottled water service", "Catering", "event", 25.00),
    ("INT-BOOTH", "Simultaneous interpretation booth", "Interpretation", "day", 450.00),
    ("INT-STAFF", "Interpreter (Arabic/English)", "Interpretation", "day", 380.00),
    ("AV-TECH", "AV technician on standby", "IT & AV", "hour", 40.00),
    ("AV-HYBRID", "Hybrid meeting setup (Teams/Zoom)", "IT & AV", "event", 120.00),
    ("AV-RECORD", "Session recording and editing", "IT & AV", "event", 150.00),
    ("SEC-GUARD", "Additional security officer", "Security", "hour", 22.00),
    ("SEC-ACCRED", "Visitor accreditation desk", "Security", "event", 90.00),
    ("FAC-CLEAN", "Deep cleaning after event", "Facilities", "event", 75.00),
    ("FAC-SETUP", "Bespoke room configuration", "Facilities", "event", 60.00),
]

ORGANIZATIONS = [
    # UNDP projects
    ("PRJ-00142", "Sudan Resilience and Recovery Programme", OrgType.UNDP_PROJECT,
     "00142897", "OUT-01", "Government of Sweden", None, None, None),
    ("PRJ-00218", "Governance and Rule of Law Project", OrgType.UNDP_PROJECT,
     "00218455", "OUT-03", "European Union", None, None, None),
    ("PRJ-00301", "Climate Adaptation and Energy Access", OrgType.UNDP_PROJECT,
     "00301220", "OUT-02", "Green Climate Fund", None, None, None),
    ("UNDP-CO", "UNDP Country Office — Operations", OrgType.UNDP_PROJECT,
     "00011000", "OUT-00", None, None, None, None),
    # UN agencies
    ("UNICEF", "United Nations Children's Fund", OrgType.UN_AGENCY, None, None, None,
     "Amira Hassan", "unicef.focal@un.org", "UN House, Khartoum"),
    ("WFP", "World Food Programme", OrgType.UN_AGENCY, None, None, None,
     "Daniel Okoth", "wfp.focal@un.org", "UN Compound, Khartoum"),
    ("UNHCR", "UN High Commissioner for Refugees", OrgType.UN_AGENCY, None, None, None,
     "Leila Mansour", "unhcr.focal@un.org", "UN House, Khartoum"),
    ("UNFPA", "UN Population Fund", OrgType.UN_AGENCY, None, None, None,
     "Peter Adeyemi", "unfpa.focal@un.org", "UN House, Khartoum"),
    ("WHO", "World Health Organization", OrgType.UN_AGENCY, None, None, None,
     "Sara Ibrahim", "who.focal@un.org", "WHO Office, Khartoum"),
    # Other
    ("MOFEP", "Ministry of Finance and Economic Planning", OrgType.GOVERNMENT, None, None, None,
     "Osman Tahir", "protocol@mofep.gov.sd", "Khartoum"),
    ("IRC-NGO", "International Rescue Committee", OrgType.EXTERNAL, None, None, None,
     "Grace Miller", "grace.miller@rescue.org", "Khartoum"),
]

USERS = [
    ("admin@undp.org", "Muawia Bashir", Role.SYSTEM_ADMIN, None),
    ("facility@undp.org", "Tarek El-Amin", Role.FACILITY_MANAGER, None),
    ("finance@undp.org", "Nadia Suleiman", Role.FINANCE_OFFICER, None),
    ("auditor@undp.org", "Joseph Kamau", Role.AUDITOR, None),
    ("unicef.focal@un.org", "Amira Hassan", Role.AGENCY_FOCAL_POINT, "UNICEF"),
    ("wfp.focal@un.org", "Daniel Okoth", Role.AGENCY_FOCAL_POINT, "WFP"),
    ("requester@undp.org", "Yasmin Abdalla", Role.REQUESTER, "PRJ-00142"),
]

MEETING_TITLES = [
    "UNCT Programme Coordination Meeting", "Quarterly Donor Review", "Project Board Meeting",
    "Inter-Agency Security Briefing", "Results-Based Management Training",
    "Joint Programme Steering Committee", "Humanitarian Country Team Meeting",
    "Operations Management Team", "Gender Working Group", "Monitoring & Evaluation Workshop",
    "Partner Consultation Workshop", "Annual Work Plan Retreat", "Procurement Review Panel",
    "Community Resilience Learning Session", "Risk Management Committee",
]


def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        ensure_rate_cards(db)

        if db.scalar(select(Room).limit(1)) is None:
            for code, name, location, capacity, features, hourly, half, full in ROOMS:
                db.add(Room(code=code, name=name, location=location, capacity=capacity,
                            features=features, rate_hourly=hourly, rate_half_day=half,
                            rate_full_day=full))
        if db.scalar(select(ServiceItem).limit(1)) is None:
            for code, name, category, unit, price in SERVICES:
                db.add(ServiceItem(code=code, name=name, category=category, unit=unit,
                                   unit_price=price))
        if db.scalar(select(Organization).limit(1)) is None:
            for (code, name, org_type, project_number, output_id, donor,
                 fp_name, fp_email, address) in ORGANIZATIONS:
                db.add(Organization(code=code, name=name, org_type=org_type,
                                    project_number=project_number, output_id=output_id,
                                    donor=donor, focal_point_name=fp_name,
                                    focal_point_email=fp_email, billing_address=address))
        db.commit()

        orgs = {o.code: o for o in db.scalars(select(Organization))}
        if db.scalar(select(User).limit(1)) is None:
            for email, full_name, role, org_code in USERS:
                db.add(User(email=email, full_name=full_name, role=role,
                            organization_id=orgs[org_code].id if org_code else None,
                            password_hash=hash_password(DEMO_PASSWORD)))
            db.commit()

        if db.scalar(select(Booking).limit(1)) is not None:
            print("Bookings already present — skipping booking generation.")
            return

        rooms = list(db.scalars(select(Room)))
        services = list(db.scalars(select(ServiceItem)))
        org_list = list(orgs.values())
        requesters = list(db.scalars(select(User).where(
            User.role.in_((Role.REQUESTER, Role.AGENCY_FOCAL_POINT, Role.FACILITY_MANAGER)))))

        random.seed(7)
        today = datetime.now().replace(minute=0, second=0, microsecond=0)
        created = 0

        for day_offset in range(-75, 21):
            day = (today + timedelta(days=day_offset)).replace(hour=0)
            if day.weekday() >= 5:
                continue
            for _ in range(random.randint(0, 3)):
                room = random.choice(rooms)
                organization = random.choice(org_list)
                start_hour = random.choice([9, 10, 11, 13, 14, 15])
                duration = random.choice([1, 2, 2, 3, 4, 8])
                starts_at = day.replace(hour=start_hour)
                ends_at = starts_at + timedelta(hours=duration)

                if find_conflicts(db, room.id, starts_at, ends_at):
                    continue

                chosen = []
                for item in random.sample(services, random.randint(0, 3)):
                    if item.unit == "person":
                        qty = random.randint(5, max(5, min(60, room.capacity)))
                    elif item.unit == "hour":
                        qty = duration
                    else:
                        qty = 1
                    chosen.append({"item": item, "quantity": float(qty)})

                if day_offset < -1:
                    status = random.choices(
                        [BookingStatus.COMPLETED, BookingStatus.CANCELLED, BookingStatus.REJECTED],
                        weights=[86, 9, 5])[0]
                elif day_offset < 3:
                    status = random.choices([BookingStatus.APPROVED, BookingStatus.PENDING],
                                            weights=[85, 15])[0]
                else:
                    status = random.choices([BookingStatus.APPROVED, BookingStatus.PENDING],
                                            weights=[60, 40])[0]

                q = quote(db, room, organization.org_type, duration, chosen)
                booking = Booking(
                    reference=next_reference(db),
                    title=random.choice(MEETING_TITLES),
                    purpose="Regular programme business.",
                    room_id=room.id,
                    organization_id=organization.id,
                    requester_id=random.choice(requesters).id,
                    starts_at=starts_at, ends_at=ends_at,
                    attendees=random.randint(5, room.capacity),
                    status=status,
                    rate_basis=q.rate_basis,
                    room_charge=q.room_charge,
                    services_charge=q.services_charge,
                    admin_fee=q.admin_fee,
                    total_charge=q.total,
                )
                if status in (BookingStatus.CANCELLED, BookingStatus.REJECTED):
                    booking.room_charge = booking.services_charge = 0.0
                    booking.admin_fee = booking.total_charge = 0.0

                db.add(booking)
                db.flush()
                if booking.total_charge:
                    for line in q.service_lines:
                        db.add(BookingService(
                            booking_id=booking.id, service_item_id=line["item"].id,
                            quantity=line["quantity"], unit_price=line["unit_price"],
                            line_total=line["line_total"],
                        ))
                created += 1
        db.commit()

        print("Seeded %d rooms, %d services, %d counterparts, %d users, %d bookings."
              % (len(rooms), len(services), len(org_list), len(USERS), created))
        print("Sign in with any demo account, password: %s" % DEMO_PASSWORD)
    finally:
        db.close()


if __name__ == "__main__":
    if "--reset" in sys.argv:
        reset_database()
        print("Database reset.")
    seed()
