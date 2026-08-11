from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .config import BASE_DIR, SECRET_KEY, SESSION_HTTPS_ONLY, SESSION_MAX_AGE_SECONDS
from .database import Base, SessionLocal, engine
from .pricing import ensure_rate_cards
from .routers import admin, auth, bookings, dashboard, finance, reference
from .templating import templates

app = FastAPI(title="Conference Room Booking & Cost Recovery System", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=SESSION_MAX_AGE_SECONDS,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(bookings.router)
app.include_router(finance.router)
app.include_router(reference.router)
app.include_router(admin.router)


@app.on_event("startup")
def on_startup():
    # Several gunicorn workers boot at once, so two of them can issue CREATE TABLE
    # for the same table concurrently. Whichever loses the race sees a duplicate
    # error that is harmless — the table it wanted now exists.
    try:
        Base.metadata.create_all(bind=engine)
    except (OperationalError, ProgrammingError, IntegrityError):
        Base.metadata.create_all(bind=engine, checkfirst=True)

    db = SessionLocal()
    try:
        ensure_rate_cards(db)
    except IntegrityError:
        db.rollback()   # another worker seeded the rate cards first
    finally:
        db.close()


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401:
        return RedirectResponse("/login?next=%s" % request.url.path, status_code=303)
    if exc.status_code in (403, 404):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"code": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code,
        )
    return HTMLResponse("<h1>%s</h1><p>%s</p>" % (exc.status_code, exc.detail),
                        status_code=exc.status_code)
