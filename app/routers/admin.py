"""Governance: user administration, access matrix and audit trail."""
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AuditLog, Organization, Role, User
from ..security import (
    PERMISSION_DESCRIPTIONS, PERMISSIONS, audit, hash_password, require,
)
from ..templating import flash, render

router = APIRouter()


@router.get("/admin/users")
def users(request: Request, db: Session = Depends(get_db), user=Depends(require("user.manage"))):
    return render(request, db, user, "users.html", "users",
                  users=list(db.scalars(select(User).order_by(User.full_name))),
                  roles=list(Role),
                  orgs=list(db.scalars(select(Organization).where(Organization.is_active.is_(True))
                                       .order_by(Organization.name))))


@router.post("/admin/users")
def create_user(request: Request, email: str = Form(...), full_name: str = Form(...),
                role: str = Form(...), organization_id: str = Form(""),
                db: Session = Depends(get_db), user=Depends(require("user.manage"))):
    email = email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        flash(request, "error", "An account already exists for %s." % email)
        return RedirectResponse("/admin/users", status_code=303)

    role_enum = Role(role)
    org_id = int(organization_id) if organization_id else None
    if role_enum in (Role.AGENCY_FOCAL_POINT, Role.REQUESTER) and org_id is None:
        flash(request, "error",
              "%s accounts must be tied to a counterpart — their access is scoped to it."
              % role_enum.label)
        return RedirectResponse("/admin/users", status_code=303)

    temp_password = secrets.token_urlsafe(9) + "Aa1!"
    new_user = User(
        email=email, full_name=full_name.strip(), role=role_enum, organization_id=org_id,
        password_hash=hash_password(temp_password), must_change_password=True,
    )
    db.add(new_user)
    db.commit()

    audit(db, request, user, "USER_CREATE", "User", new_user.email,
          "Role %s, counterpart %s" % (role_enum.value, org_id))
    flash(request, "success",
          "Account created for %s. Temporary password: %s — it must be changed at first sign-in."
          % (email, temp_password))
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/role")
def change_role(user_id: int, request: Request, role: str = Form(...),
                organization_id: str = Form(""), db: Session = Depends(get_db),
                user=Depends(require("user.manage"))):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found.")
    if target.id == user.id and Role(role) != Role.SYSTEM_ADMIN:
        flash(request, "error", "You cannot remove your own administrator role.")
        return RedirectResponse("/admin/users", status_code=303)

    before = target.role.value
    target.role = Role(role)
    target.organization_id = int(organization_id) if organization_id else None
    db.commit()
    audit(db, request, user, "USER_ROLE_CHANGE", "User", target.email,
          "%s -> %s" % (before, target.role.value))
    flash(request, "success", "%s is now a %s." % (target.full_name, target.role.label))
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/toggle")
def toggle_user(user_id: int, request: Request, db: Session = Depends(get_db),
                user=Depends(require("user.manage"))):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found.")
    if target.id == user.id:
        flash(request, "error", "You cannot disable your own account.")
        return RedirectResponse("/admin/users", status_code=303)

    target.is_active = not target.is_active
    target.failed_logins = 0
    target.locked_until = None
    db.commit()
    audit(db, request, user, "USER_TOGGLE", "User", target.email,
          "Account %s" % ("enabled" if target.is_active else "disabled"))
    flash(request, "success", "%s has been %s."
          % (target.full_name, "re-enabled" if target.is_active else "disabled"))
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/reset")
def reset_password(user_id: int, request: Request, db: Session = Depends(get_db),
                   user=Depends(require("user.manage"))):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found.")
    temp_password = secrets.token_urlsafe(9) + "Aa1!"
    target.password_hash = hash_password(temp_password)
    target.must_change_password = True
    target.failed_logins = 0
    target.locked_until = None
    db.commit()
    audit(db, request, user, "USER_PASSWORD_RESET", "User", target.email, "Administrative reset")
    flash(request, "success", "Temporary password for %s: %s" % (target.email, temp_password))
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/admin/access")
def access_matrix(request: Request, db: Session = Depends(get_db),
                  user=Depends(require("user.manage"))):
    roles = list(Role)
    matrix = [
        {"permission": perm, "description": PERMISSION_DESCRIPTIONS.get(perm, ""),
         "allowed": {r: (r in PERMISSIONS[perm]) for r in roles}}
        for perm in PERMISSIONS
    ]
    return render(request, db, user, "access_matrix.html", "access", roles=roles, matrix=matrix)


@router.get("/admin/audit")
def audit_trail(request: Request, action: str = "", actor: str = "", outcome: str = "",
                db: Session = Depends(get_db), user=Depends(require("audit.view"))):
    stmt = select(AuditLog).order_by(AuditLog.at.desc())
    if action:
        stmt = stmt.where(AuditLog.action.ilike("%%%s%%" % action))
    if actor:
        stmt = stmt.where(AuditLog.actor_email.ilike("%%%s%%" % actor))
    if outcome:
        stmt = stmt.where(AuditLog.outcome == outcome)
    entries = list(db.scalars(stmt.limit(400)))
    actions = sorted({e.action for e in db.scalars(select(AuditLog).limit(2000))})
    return render(request, db, user, "audit.html", "audit",
                  entries=entries, actions=actions,
                  f_action=action, f_actor=actor, f_outcome=outcome)
