import csv
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import CURRENCY
from ..database import get_db
from ..models import (
    Booking, BookingStatus, Invoice, InvoiceStatus, OrgType, Organization, Room, utcnow,
)
from ..security import audit, has_permission, require, visible_org_ids
from ..sqlutil import duration_hours
from ..templating import flash, render
from ..workflow import build_invoice, uninvoiced_bookings

router = APIRouter()


def _month_bounds(value: str):
    """value is 'YYYY-MM'. Returns (start, end-exclusive)."""
    try:
        start = datetime.strptime(value, "%Y-%m")
    except (ValueError, TypeError):
        now = utcnow()
        start = datetime(now.year, now.month, 1)
    end = datetime(start.year + (start.month == 12), (start.month % 12) + 1, 1)
    return start, end


@router.get("/invoices")
def list_invoices(request: Request, status: str = "", db: Session = Depends(get_db),
                  user=Depends(require("invoice.view.own"))):
    stmt = select(Invoice).order_by(Invoice.created_at.desc())
    if not has_permission(user, "invoice.view.all"):
        stmt = stmt.where(Invoice.organization_id == (user.organization_id or -1))
    if status:
        stmt = stmt.where(Invoice.status == InvoiceStatus(status))
    invoices = list(db.scalars(stmt.limit(300)))

    orgs = []
    if has_permission(user, "invoice.manage"):
        orgs = list(db.scalars(select(Organization).where(Organization.is_active.is_(True))
                               .order_by(Organization.name)))
    return render(request, db, user, "invoices_list.html", "invoices",
                  invoices=invoices, statuses=list(InvoiceStatus), f_status=status,
                  orgs=orgs, default_month=utcnow().strftime("%Y-%m"))


@router.post("/invoices/generate")
def generate_invoice(request: Request, organization_id: int = Form(...), month: str = Form(...),
                     db: Session = Depends(get_db), user=Depends(require("invoice.manage"))):
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise HTTPException(404, "Counterpart not found.")
    start, end = _month_bounds(month)
    invoice, error = build_invoice(db, organization, start, end, user)
    if error:
        flash(request, "warn", error)
        return RedirectResponse("/invoices", status_code=303)
    audit(db, request, user, "INVOICE_CREATE", "Invoice", invoice.number,
          "%s %s — %d booking(s), total %.2f"
          % (organization.name, month, len(invoice.bookings), invoice.total))
    flash(request, "success", "Draft invoice %s generated for %s."
          % (invoice.number, organization.name))
    return RedirectResponse("/invoices/%d" % invoice.id, status_code=303)


@router.get("/invoices/{invoice_id}")
def invoice_detail(invoice_id: int, request: Request, db: Session = Depends(get_db),
                   user=Depends(require("invoice.view.own"))):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "Invoice not found.")
    if not has_permission(user, "invoice.view.all") and invoice.organization_id != user.organization_id:
        raise HTTPException(403, "This invoice belongs to another counterpart.")
    return render(request, db, user, "invoice_detail.html", "invoices", invoice=invoice)


@router.post("/invoices/{invoice_id}/status")
def set_invoice_status(invoice_id: int, request: Request, action: str = Form(...),
                       db: Session = Depends(get_db), user=Depends(require("invoice.manage"))):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "Invoice not found.")

    transitions = {
        "issue": (InvoiceStatus.DRAFT, InvoiceStatus.ISSUED),
        "settle": (InvoiceStatus.ISSUED, InvoiceStatus.PAID),
        "void": (None, InvoiceStatus.VOID),
    }
    if action not in transitions:
        raise HTTPException(400, "Unknown action.")
    required_from, new_status = transitions[action]
    if required_from is not None and invoice.status != required_from:
        flash(request, "error", "Invoice %s is %s and cannot be %sd."
              % (invoice.number, invoice.status.value.lower(), action))
        return RedirectResponse("/invoices/%d" % invoice_id, status_code=303)
    if invoice.status == InvoiceStatus.PAID and action == "void":
        flash(request, "error", "A settled invoice cannot be voided; raise a credit note instead.")
        return RedirectResponse("/invoices/%d" % invoice_id, status_code=303)

    invoice.status = new_status
    if new_status == InvoiceStatus.ISSUED:
        invoice.issued_at = utcnow()
    elif new_status == InvoiceStatus.PAID:
        invoice.paid_at = utcnow()
    elif new_status == InvoiceStatus.VOID:
        for booking in invoice.bookings:
            booking.invoice_id = None
    db.commit()
    audit(db, request, user, "INVOICE_%s" % new_status.value, "Invoice", invoice.number,
          "Total %.2f" % invoice.total)
    flash(request, "success", "Invoice %s marked %s." % (invoice.number, new_status.value.lower()))
    return RedirectResponse("/invoices/%d" % invoice_id, status_code=303)


@router.get("/reports")
def reports(request: Request, month: str = "", db: Session = Depends(get_db),
            user=Depends(require("report.view"))):
    month = month or utcnow().strftime("%Y-%m")
    start, end = _month_bounds(month)
    billable = (BookingStatus.APPROVED, BookingStatus.COMPLETED)

    by_org = list(db.execute(
        select(Organization.name, Organization.org_type, Organization.code,
               func.count(Booking.id), func.sum(Booking.total_charge),
               func.sum(Booking.waiver_amount))
        .join(Booking, Booking.organization_id == Organization.id)
        .where(Booking.status.in_(billable), Booking.starts_at >= start, Booking.starts_at < end)
        .group_by(Organization.id)
        .order_by(func.sum(Booking.total_charge).desc())
    ))

    by_room = list(db.execute(
        select(Room.name, func.count(Booking.id), func.sum(Booking.total_charge),
               func.sum(duration_hours(Booking.starts_at, Booking.ends_at)))
        .join(Booking, Booking.room_id == Room.id)
        .where(Booking.status.in_(billable), Booking.starts_at >= start, Booking.starts_at < end)
        .group_by(Room.id)
        .order_by(func.sum(Booking.total_charge).desc())
    ))

    by_type = list(db.execute(
        select(Organization.org_type, func.count(Booking.id), func.sum(Booking.total_charge))
        .join(Booking, Booking.organization_id == Organization.id)
        .where(Booking.status.in_(billable), Booking.starts_at >= start, Booking.starts_at < end)
        .group_by(Organization.org_type)
    ))

    grand_total = sum((r[4] or 0) for r in by_org)
    waived_total = sum((r[5] or 0) for r in by_org)
    unbilled = db.scalar(
        select(func.sum(Booking.total_charge))
        .where(Booking.status.in_(billable), Booking.invoice_id.is_(None))
    ) or 0.0

    # Six-month trend
    trend = []
    cursor = start
    for _ in range(6):
        cursor = datetime(cursor.year - (cursor.month == 1), ((cursor.month - 2) % 12) + 1, 1)
        nxt = datetime(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)
        value = db.scalar(
            select(func.sum(Booking.total_charge))
            .where(Booking.status.in_(billable), Booking.starts_at >= cursor,
                   Booking.starts_at < nxt)
        ) or 0.0
        trend.append({"label": cursor.strftime("%b %y"), "value": value})
    trend.reverse()
    trend.append({"label": start.strftime("%b %y"), "value": grand_total})
    trend_max = max([t["value"] for t in trend]) or 1

    return render(request, db, user, "reports.html", "reports",
                  month=month, month_label=start.strftime("%B %Y"),
                  by_org=by_org, by_room=by_room, by_type=by_type,
                  grand_total=grand_total, waived_total=waived_total, unbilled=unbilled,
                  trend=trend, trend_max=trend_max)


@router.get("/reports/export.csv")
def export_csv(request: Request, month: str = "", db: Session = Depends(get_db),
               user=Depends(require("report.view"))):
    month = month or utcnow().strftime("%Y-%m")
    start, end = _month_bounds(month)
    bookings = list(db.scalars(
        select(Booking)
        .where(Booking.starts_at >= start, Booking.starts_at < end)
        .order_by(Booking.starts_at)
    ))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Reference", "Title", "Room", "Counterpart", "Counterpart type", "Charge reference",
        "Start", "End", "Hours", "Status", "Rate basis",
        "Room charge", "Services", "Management fee", "Waiver", "Total (%s)" % CURRENCY,
        "Invoice",
    ])
    for b in bookings:
        writer.writerow([
            b.reference, b.title, b.room.name, b.organization.name, b.organization.type_label,
            b.organization.charge_reference,
            b.starts_at.strftime("%Y-%m-%d %H:%M"), b.ends_at.strftime("%Y-%m-%d %H:%M"),
            "%.2f" % b.duration_hours, b.status.value, b.rate_basis,
            "%.2f" % b.room_charge, "%.2f" % b.services_charge, "%.2f" % b.admin_fee,
            "%.2f" % b.waiver_amount, "%.2f" % b.total_charge,
            b.invoice.number if b.invoice else "",
        ])
    buffer.seek(0)
    audit(db, request, user, "REPORT_EXPORT", "Report", month, "%d rows" % len(bookings))
    return StreamingResponse(
        iter([buffer.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="cost-recovery-%s.csv"' % month},
    )
