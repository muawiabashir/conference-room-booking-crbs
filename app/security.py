"""Access governance: authentication, role-based authorisation, scoping and audit."""
from datetime import datetime, timedelta
import re

from fastapi import Depends, HTTPException, Request, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import LOCKOUT_MINUTES, MAX_FAILED_LOGINS, MIN_PASSWORD_LENGTH
from .database import get_db
from .models import AuditLog, OrgType, Role, User, utcnow

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(raw: str) -> str:
    return pwd_context.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return pwd_context.verify(raw, hashed)


def password_policy_errors(raw: str):
    errors = []
    if len(raw) < MIN_PASSWORD_LENGTH:
        errors.append("must be at least %d characters" % MIN_PASSWORD_LENGTH)
    if not re.search(r"[A-Z]", raw):
        errors.append("must contain an uppercase letter")
    if not re.search(r"[a-z]", raw):
        errors.append("must contain a lowercase letter")
    if not re.search(r"\d", raw):
        errors.append("must contain a digit")
    if not re.search(r"[^A-Za-z0-9]", raw):
        errors.append("must contain a symbol")
    return errors


# --------------------------------------------------------------------------------------
# Permission matrix. Every guarded action maps to the set of roles allowed to perform it.
# --------------------------------------------------------------------------------------
PERMISSIONS = {
    # Bookings
    "booking.view.own":     {Role.SYSTEM_ADMIN, Role.FACILITY_MANAGER, Role.FINANCE_OFFICER,
                             Role.AGENCY_FOCAL_POINT, Role.REQUESTER, Role.AUDITOR},
    "booking.view.all":     {Role.SYSTEM_ADMIN, Role.FACILITY_MANAGER, Role.FINANCE_OFFICER,
                             Role.AUDITOR},
    "booking.create":       {Role.SYSTEM_ADMIN, Role.FACILITY_MANAGER, Role.AGENCY_FOCAL_POINT,
                             Role.REQUESTER},
    "booking.edit":         {Role.SYSTEM_ADMIN, Role.FACILITY_MANAGER, Role.AGENCY_FOCAL_POINT,
                             Role.REQUESTER},
    "booking.approve":      {Role.SYSTEM_ADMIN, Role.FACILITY_MANAGER},
    "booking.cancel":       {Role.SYSTEM_ADMIN, Role.FACILITY_MANAGER, Role.AGENCY_FOCAL_POINT,
                             Role.REQUESTER},
    "booking.waive":        {Role.SYSTEM_ADMIN, Role.FINANCE_OFFICER},
    # Reference data
    "room.manage":          {Role.SYSTEM_ADMIN, Role.FACILITY_MANAGER},
    "service.manage":       {Role.SYSTEM_ADMIN, Role.FACILITY_MANAGER},
    "org.view":             {Role.SYSTEM_ADMIN, Role.FACILITY_MANAGER, Role.FINANCE_OFFICER,
                             Role.AUDITOR},
    "org.manage":           {Role.SYSTEM_ADMIN, Role.FINANCE_OFFICER},
    "ratecard.manage":      {Role.SYSTEM_ADMIN, Role.FINANCE_OFFICER},
    # Finance
    "invoice.view.own":     {Role.SYSTEM_ADMIN, Role.FINANCE_OFFICER, Role.AGENCY_FOCAL_POINT,
                             Role.AUDITOR},
    "invoice.view.all":     {Role.SYSTEM_ADMIN, Role.FINANCE_OFFICER, Role.AUDITOR},
    "invoice.manage":       {Role.SYSTEM_ADMIN, Role.FINANCE_OFFICER},
    "report.view":          {Role.SYSTEM_ADMIN, Role.FACILITY_MANAGER, Role.FINANCE_OFFICER,
                             Role.AUDITOR},
    # Governance
    "user.manage":          {Role.SYSTEM_ADMIN},
    "audit.view":           {Role.SYSTEM_ADMIN, Role.AUDITOR},
}

PERMISSION_DESCRIPTIONS = {
    "booking.view.own": "See bookings belonging to own counterpart",
    "booking.view.all": "See every booking across all counterparts",
    "booking.create": "Raise a new room request",
    "booking.edit": "Amend a request before approval",
    "booking.approve": "Approve or reject a pending request",
    "booking.cancel": "Cancel a booking",
    "booking.waive": "Apply a cost-recovery waiver",
    "room.manage": "Create and edit rooms and rate cards for rooms",
    "service.manage": "Maintain the chargeable service catalogue",
    "org.view": "View counterparts (agencies, projects)",
    "org.manage": "Create and edit counterparts and their charge codes",
    "ratecard.manage": "Set cost-recovery multipliers and management fees",
    "invoice.view.own": "View own counterpart's invoices",
    "invoice.view.all": "View all invoices",
    "invoice.manage": "Generate, issue, settle and void invoices",
    "report.view": "Access utilisation and revenue reporting",
    "user.manage": "Create users, assign roles, disable accounts",
    "audit.view": "Read the immutable audit trail",
}


def has_permission(user: User, permission: str) -> bool:
    allowed = PERMISSIONS.get(permission)
    if allowed is None:
        raise KeyError("unknown permission: %s" % permission)
    return user.role in allowed


def permissions_for(role: Role):
    return sorted(p for p, roles in PERMISSIONS.items() if role in roles)


# --------------------------------------------------------------------------------------
# Session / current user
# --------------------------------------------------------------------------------------
def _client_ip(request: Request) -> str:
    if request.client:
        return request.client.host
    return ""


def audit(db: Session, request: Request, actor, action: str, entity_type: str = "",
          entity_id="", detail: str = "", outcome: str = "SUCCESS"):
    db.add(AuditLog(
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", "anonymous"),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        detail=detail,
        ip_address=_client_ip(request),
        outcome=outcome,
    ))
    db.commit()


def load_user(request: Request, db: Session):
    user_id = request.session.get("uid")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        return None
    return user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = load_user(request, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign-in required")
    return user


def require(permission: str):
    """Dependency factory enforcing a single permission."""
    def dependency(request: Request, db: Session = Depends(get_db)) -> User:
        user = load_user(request, db)
        if user is None:
            raise HTTPException(status_code=401, detail="Sign-in required")
        if not has_permission(user, permission):
            audit(db, request, user, "ACCESS_DENIED", "Permission", permission,
                  "Role %s attempted %s" % (user.role.value, permission), outcome="DENIED")
            raise HTTPException(
                status_code=403,
                detail="Your role (%s) is not authorised to %s."
                       % (user.role.label, PERMISSION_DESCRIPTIONS.get(permission, permission)),
            )
        return user
    return dependency


def visible_org_ids(user: User):
    """Row-level scoping. None means unrestricted."""
    if has_permission(user, "booking.view.all"):
        return None
    return {user.organization_id} if user.organization_id else set()


def assert_can_touch_org(user: User, organization_id: int):
    scope = visible_org_ids(user)
    if scope is not None and organization_id not in scope:
        raise HTTPException(
            status_code=403,
            detail="You may only act on records belonging to your own counterpart.",
        )


# --------------------------------------------------------------------------------------
# Login flow with lockout
# --------------------------------------------------------------------------------------
def authenticate(db: Session, request: Request, email: str, password: str):
    """Returns (user, error_message)."""
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    generic = "Invalid credentials."
    if user is None:
        audit(db, request, None, "LOGIN", "User", email, "Unknown account", outcome="DENIED")
        return None, generic
    if not user.is_active:
        audit(db, request, user, "LOGIN", "User", user.id, "Disabled account", outcome="DENIED")
        return None, "This account has been disabled. Contact the system administrator."
    if user.locked_until and user.locked_until > utcnow():
        mins = int((user.locked_until - utcnow()).total_seconds() // 60) + 1
        audit(db, request, user, "LOGIN", "User", user.id, "Locked account", outcome="DENIED")
        return None, "Account locked. Try again in %d minute(s)." % mins

    if not verify_password(password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until = utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_logins = 0
            db.commit()
            audit(db, request, user, "LOCKOUT", "User", user.id,
                  "Locked after %d failed attempts" % MAX_FAILED_LOGINS, outcome="DENIED")
            return None, "Too many failed attempts. Account locked for %d minutes." % LOCKOUT_MINUTES
        db.commit()
        audit(db, request, user, "LOGIN", "User", user.id, "Bad password", outcome="DENIED")
        return None, generic

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    db.commit()
    request.session.clear()
    request.session["uid"] = user.id
    audit(db, request, user, "LOGIN", "User", user.id, "Signed in as %s" % user.role.value)
    return user, None
