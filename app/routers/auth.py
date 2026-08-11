from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..config import CURRENCY, DUTY_STATION, ORGANISATION_NAME
from ..database import get_db
from ..security import (
    audit, current_user, hash_password, load_user, password_policy_errors, verify_password,
    authenticate,
)
from ..templating import flash, templates

router = APIRouter()


@router.get("/login")
def login_form(request: Request, next: str = "/", db: Session = Depends(get_db)):
    if load_user(request, db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {
        "next": next, "error": None,
        "org_name": ORGANISATION_NAME, "duty_station": DUTY_STATION, "currency": CURRENCY,
    })


@router.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...),
                 next: str = Form("/"), db: Session = Depends(get_db)):
    user, error = authenticate(db, request, email, password)
    if error:
        return templates.TemplateResponse(request, "login.html", {
            "next": next, "error": error,
            "org_name": ORGANISATION_NAME, "duty_station": DUTY_STATION, "currency": CURRENCY,
        }, status_code=401)
    if user.must_change_password:
        return RedirectResponse("/account/password", status_code=303)
    target = next if next.startswith("/") else "/"
    return RedirectResponse(target, status_code=303)


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
