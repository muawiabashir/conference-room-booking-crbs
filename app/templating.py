"""Jinja environment, flash messages and navigation model."""
from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from .config import (
    BASE_DIR, CURRENCY, DUTY_STATION, ORGANISATION_NAME, PUBLIC_BASE_URL, SHOW_DEMO_CREDENTIALS,
    TIMEZONE_LABEL,
)
from .models import ORG_TYPE_LABELS, Booking, BookingStatus
from .security import has_permission

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Expose the macro module globally so child templates can use `ui.*` inside blocks,
# where a parent-template `{% import %}` would not be in scope.
templates.env.globals["ui"] = templates.env.get_template("macros.html").module
templates.env.globals["show_demo_credentials"] = SHOW_DEMO_CREDENTIALS


def public_url(request, path):
    """Absolute URL for `path`, correct even when the app is reverse-proxied
    under a path prefix (see PUBLIC_BASE_URL) — request.base_url alone would
    omit that prefix, since the app never sees it.
    """
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    return base + path


def webcal_url(request, path):
    """Same as public_url(), but with the webcal:// scheme calendar apps use
    to recognise a subscribable feed."""
    url = public_url(request, path)
    return "webcal://" + url.split("://", 1)[1]


templates.env.globals["public_url"] = public_url
templates.env.globals["webcal_url"] = webcal_url


def flash(request: Request, level: str = None, message: str = None):
    """Push a message when called with args; drain the queue when called with none."""
    if message is not None:
        request.session.setdefault("_flash", []).append([level, message])
        return None
    messages = request.session.pop("_flash", [])
    return [(lvl, msg) for lvl, msg in messages]


NAV = [
    ("Operations", [
        ("dashboard", "Dashboard", "/", "grid", None),
        ("calendar", "Room Calendar", "/calendar", "calendar", None),
        ("bookings", "Bookings", "/bookings", "list", "booking.view.own"),
    ]),
    ("Cost Recovery", [
        ("invoices", "Invoices", "/invoices", "receipt", "invoice.view.own"),
        ("reports", "Reports", "/reports", "chart", "report.view"),
        ("ratecards", "Rate Cards", "/rate-cards", "scale", "ratecard.manage"),
    ]),
    ("Reference Data", [
        ("rooms", "Rooms", "/rooms", "door", None),
        ("services", "Service Catalogue", "/services", "tag", None),
        ("organizations", "Counterparts", "/organizations", "building", "org.view"),
    ]),
    ("Governance", [
        ("users", "Users & Roles", "/admin/users", "users", "user.manage"),
        ("access", "Access Matrix", "/admin/access", "shield", "user.manage"),
        ("audit", "Audit Trail", "/admin/audit", "shield", "audit.view"),
    ]),
]


def build_nav(db):
    def nav_sections(user):
        pending = db.scalar(
            select(func.count()).select_from(Booking)
            .where(Booking.status == BookingStatus.PENDING)
        ) or 0
        sections = []
        for title, items in NAV:
            visible = []
            for key, label, url, icon, perm in items:
                if perm and not has_permission(user, perm):
                    continue
                badge = None
                if key == "bookings" and pending and has_permission(user, "booking.approve"):
                    badge = pending
                visible.append({"key": key, "label": label, "url": url,
                                "icon": icon, "badge": badge})
            if visible:
                sections.append({"title": title, "links": visible})
        return sections
    return nav_sections


def render(request: Request, db, user, template: str, active_nav: str, **context):
    context.update({
        "request": request,
        "user": user,
        "active_nav": active_nav,
        "flash": flash,
        "nav_sections": build_nav(db),
        "can": lambda perm: has_permission(user, perm),
        "org_type_label": lambda value: ORG_TYPE_LABELS.get(value, value),
        "org_name": ORGANISATION_NAME,
        "duty_station": DUTY_STATION,
        "tz_label": TIMEZONE_LABEL,
        "currency": CURRENCY,
    })
    return templates.TemplateResponse(request, template, context)
