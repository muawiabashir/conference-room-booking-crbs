from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import CURRENCY, DUTY_STATION, ORGANISATION_NAME, PUBLIC_BASE_URL, SSO_ENABLED
from ..database import get_db
from ..models import User, utcnow
from ..security import (
    audit, current_user, hash_password, load_user, password_policy_errors, verify_password,
    authenticate,
)
from ..sso import oauth
from ..templating import flash, templates

router = APIRouter()


def _login_context(next, error):
    return {
        "next": next, "error": error, "sso_enabled": SSO_ENABLED,
        "org_name": ORGANISATION_NAME, "duty_station": DUTY_STATION, "currency": CURRENCY,
    }


@router.get("/login")
def login_form(request: Request, next: str = "/", db: Session = Depends(get_db)):
    if load_user(request, db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", _login_context(next, None))


@router.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...),
                 next: str = Form("/"), db: Session = Depends(get_db)):
    user, error = authenticate(db, request, email, password)
    if error:
        return templates.TemplateResponse(
            request, "login.html", _login_context(next, error), status_code=401)
    if user.must_change_password:
        return RedirectResponse("/account/password", status_code=303)
    target = next if next.startswith("/") else "/"
    return RedirectResponse(target, status_code=303)


@router.get("/auth/microsoft/login")
async def microsoft_login(request: Request, next: str = "/"):
    if not SSO_ENABLED:
        raise HTTPException(status_code=404)
    request.session["sso_next"] = next if next.startswith("/") else "/"
    redirect_uri = (PUBLIC_BASE_URL + "/auth/microsoft/callback" if PUBLIC_BASE_URL
                    else str(request.url_for("microsoft_callback")))
    return await oauth.microsoft.authorize_redirect(request, redirect_uri)


@router.get("/auth/microsoft/callback", name="microsoft_callback")
async def microsoft_callback(request: Request, db: Session = Depends(get_db)):
    if not SSO_ENABLED:
        raise HTTPException(status_code=404)
    next_url = request.session.pop("sso_next", "/")

    try:
        token = await oauth.microsoft.authorize_access_token(request)
    except OAuthError as exc:
        return templates.TemplateResponse(
            request, "login.html",
            _login_context(next_url, "Microsoft sign-in failed: %s" % exc.description),
            status_code=401,
        )

    claims = token.get("userinfo") or {}
    subject = claims.get("oid") or claims.get("sub") or ""
    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()

    user = None
    if subject:
        user = db.scalar(select(User).where(User.sso_subject == subject))
    if user is None and email:
        user = db.scalar(select(User).where(User.email == email))

    if user is None:
        audit(db, request, None, "LOGIN", "User", email or subject or "unknown",
              "Microsoft SSO: no matching account", outcome="DENIED")
        return templates.TemplateResponse(
            request, "login.html",
            _login_context(next_url, "No account found for %s. Ask your system administrator "
                                      "to add you under Users & Roles." % (email or "your account")),
            status_code=403,
        )

    if not user.is_active:
        audit(db, request, user, "LOGIN", "User", user.id,
              "Microsoft SSO: disabled account", outcome="DENIED")
        return templates.TemplateResponse(
            request, "login.html",
            _login_context(next_url, "This account has been disabled. Contact the system administrator."),
            status_code=403,
        )

    if subject and user.sso_subject != subject:
        user.sso_subject = subject

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    db.commit()
    request.session.clear()
    request.session["uid"] = user.id
    audit(db, request, user, "LOGIN", "User", user.id, "Signed in via Microsoft SSO")

    if user.must_change_password:
        return RedirectResponse("/account/password", status_code=303)
    return RedirectResponse(next_url, status_code=303)


@router.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user = load_user(request, db)
    if user:
        audit(db, request, user, "LOGOUT", "User", user.id, "Signed out")
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/account/password")
def password_form(request: Request, db: Session = Depends(get_db), user=Depends(current_user)):
    return templates.TemplateResponse(request, "password.html", {
        "user": user, "errors": [],
        "org_name": ORGANISATION_NAME, "duty_station": DUTY_STATION, "currency": CURRENCY,
    })


@router.post("/account/password")
def password_submit(request: Request, current: str = Form(...), new: str = Form(...),
                    confirm: str = Form(...), db: Session = Depends(get_db),
                    user=Depends(current_user)):
    errors = []
    if not verify_password(current, user.password_hash):
        errors.append("Your current password is incorrect.")
    if new != confirm:
        errors.append("The new password and confirmation do not match.")
    errors.extend("New password %s." % e for e in password_policy_errors(new))

    if errors:
        audit(db, request, user, "PASSWORD_CHANGE", "User", user.id,
              "Rejected: %s" % "; ".join(errors), outcome="DENIED")
        return templates.TemplateResponse(request, "password.html", {
            "user": user, "errors": errors,
            "org_name": ORGANISATION_NAME, "duty_station": DUTY_STATION, "currency": CURRENCY,
        }, status_code=400)

    user.password_hash = hash_password(new)
    user.must_change_password = False
    db.commit()
    audit(db, request, user, "PASSWORD_CHANGE", "User", user.id, "Password updated")
    flash(request, "success", "Your password has been updated.")
    return RedirectResponse("/", status_code=303)
