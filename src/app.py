"""Bibliotek — FastAPI application entry point.

Wires together all feature routers, sets up Jinja2 templates,
mounts static files, and runs startup initialisation (DB creation
+ admin seed user).
"""


from fastapi import FastAPI, HTTPException, Request
import os
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.database import init_db
from src.models import User
from src.users import register_user, update_user

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(title="Bibliotek", version="0.1.0")

# ---------------------------------------------------------------------------
# Templates & static files
# ---------------------------------------------------------------------------

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


def _template_context(request: Request) -> dict:
    """Add current_user to template context."""
    cu = _get_current_user_from_request(request)
    return {"current_user": cu}


# ---------------------------------------------------------------------------
# Startup — create tables and seed admin user
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup() -> None:
    """Initialise the database, ensure an admin account exists, and populate
    the catalog on first boot: the bundled bulk import if data/bulk_books.jsonl.gz
    ships with this checkout (production), else a tiny 3-book demo seed so a
    bare dev checkout is still instantly shoppable (see bulk_import.py)."""
    init_db()

    from src.database import get_session_cm
    from src.models import Book
    from src.books import create_book
    from src.bulk_import import run_bulk_import_if_needed

    with get_session_cm() as db:
        from src.users import update_user
        admin = db.query(User).filter(User.username == "admin").first()
        if admin is None:
            # MC 743.10 (I2): the bootstrap admin must be HARMLESS and must
            # NEVER block boot. The old seed wrote a hard-coded default
            # password (admin/admin), which 743.6 flagged and the open-login
            # DoD (743.10) makes moot — open login lets anyone in regardless.
            # So:
            #   * the password comes from env (BIBLIOTEK_ADMIN_PASSWORD) when
            #     set, else the account is created with a *random* (unguessable)
            #     password so the default-cred finding is closed;
            #   * the whole seed is wrapped in try/except and degrades to a
            #     no-op on any error (it must never crash the app at startup).
            import uuid
            admin_pw = os.getenv("BIBLIOTEK_ADMIN_PASSWORD") or uuid.uuid4().hex
            try:
                new_admin = register_user(db, username="admin", password=admin_pw)
                update_user(db, new_admin.id, role="admin")
            except Exception as exc:  # pragma: no cover - defensive, never block boot
                app.logger.warning("admin seed skipped: %s", exc)

    # Bulk catalog import (idempotent, versioned — see bulk_import.py). Runs
    # regardless of DATABASE_URL: it is gated on its own version marker, not
    # on dev-vs-prod detection, so it can never again silently fail to apply
    # in production the way the old "DATABASE_URL is None" gate on the demo
    # seed did (that gate only ever controlled the 3-book DEMO, but nothing
    # populated the real catalog in production at all).
    has_bulk_artifact = run_bulk_import_if_needed()

    if not has_bulk_artifact:
        with get_session_cm() as db:
            if db.query(Book).count() == 0:
                SAMPLE_BOOKS = [
                    {"isbn": "9789175037187", "title": "Broderna Lejonhjarta",
                     "author": "Astrid Lindgren", "publisher": "Raben Sjogren", "year": 1973,
                     "hcf_category": "hcf"},
                    {"isbn": "9789100117481", "title": "Den allvarsamma leken",
                     "author": "Hjalmar Soderberg", "publisher": "Albert Bonniers", "year": 1912,
                     "hcf_category": "adult"},
                    {"isbn": "9780141439518", "title": "Pride and Prejudice",
                     "author": "Jane Austen", "publisher": "Penguin", "year": 1813,
                     "hcf_category": "adult"},
                ]
                for b in SAMPLE_BOOKS:
                    try:
                        create_book(db, **b)
                    except Exception:
                        db.rollback()


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

def _page_info(page: int, total: int, per_page: int) -> dict:
    pages = max(1, (total + per_page - 1) // per_page)
    return {"items": [], "page": page, "pages": pages, "total": total}


def _format_book_count(n: int) -> str:
    """Swedish thousands-separated count for display (e.g. 128493 -> '128 493').

    stdlib-only (no locale dependency, which would need sv_SE installed).
    """
    return f"{n:,}".replace(",", " ")


def _get_current_user_from_request(request: Request):
    """Extract and return current user dict from cookie token, or None."""
    from src.auth import verify_token
    cookie = request.cookies.get("access_token")
    if cookie:
        data = verify_token(cookie)
        if data:
            from src.database import get_session_cm
            from src.models import User
            with get_session_cm() as db:
                user = db.query(User).filter(User.id == data["user_id"]).first()
                if user:
                    return {"user_id": user.id, "role": user.role, "username": user.username}
    return None


# ---------------------------------------------------------------------------
# Middleware — inject current_user into every request state
# ---------------------------------------------------------------------------

from datetime import UTC

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse


class CurrentUserMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> StarletteResponse:
        request.state.current_user = _get_current_user_from_request(request)
        return await call_next(request)


app.add_middleware(CurrentUserMiddleware)

# MC 2034.2 — public demo: per-visitor rate limit on mutating requests.
# Gated by ENABLE_DEMO_WRITE_GUARD (config): ON in production, OFF in the test
# suite (the 10/300s bucket would otherwise make the deterministic test run
# flaky — a burst of writes from one test IP gets a 429 mid-run, MC 743.1).
from src.config import ENABLE_DEMO_WRITE_GUARD
if ENABLE_DEMO_WRITE_GUARD:
    from src.demo_guard import DemoWriteGuard
    app.add_middleware(DemoWriteGuard)


# ---------------------------------------------------------------------------
# Home page
# ---------------------------------------------------------------------------

@app.get("/")
async def home(request: Request) -> HTMLResponse:
    """Render the home page with recent books and popular categories."""
    from src.database import get_session_cm
    from src.models import Book

    recent = []
    categories = []
    with get_session_cm() as db:
        # Real catalog size — reuses the exact same .count() idiom as
        # catalog_page()'s `total = query.count()` below. This MUST stay the
        # single source of truth for any book-count claim shown to users
        # (see 2026-08-17 fix: index.html used to hardcode "100 000" as
        # marketing copy while the db held 3 rows).
        book_count = db.query(Book).count()

        for b in db.query(Book).order_by(
            Book.id.desc()
        ).limit(12).all():
            recent.append({
                "id": b.id,
                "title": b.title or "",
                "author": b.author,
                "cover_url": b.cover_url,
            })

         # Popular categories from database (≥1000 books, sorted by count desc)
        from sqlalchemy import func as func_
        cat_counts = (
            db.query(Book.hcf_category, func_.count(Book.id))
            .filter(Book.hcf_category.isnot(None))
            .group_by(Book.hcf_category)
            .having(func_.count(Book.id) >= 1000)
            .order_by(func_.count(Book.id).desc())
            .all()
        )
        categories = [{"name": r[0], "count": r[1]} for r in cat_counts]

    return templates.TemplateResponse(
        request=request, name="index.html",
        context={
            **_template_context(request),
            "recent_books": recent,
            "popular_categories": categories,
            "book_count": _format_book_count(book_count),
        },
    )


# ---------------------------------------------------------------------------
# Catalog — book listing with search, filters, pagination
# ---------------------------------------------------------------------------

@app.get("/catalog")
async def catalog_page(
    request: Request,
    q: str = "",
    category: str = "",
    language: str = "",
    page: int = 1,
    per_page: int = 48,
) -> HTMLResponse:
    from sqlalchemy import or_

    from src.database import get_session_cm
    from src.models import Book

    with get_session_cm() as db:
        query = db.query(Book)

        # Search
        if q:
            query = query.filter(
                or_(
                    Book.title.ilike(f"%{q}%"),
                    Book.author.ilike(f"%{q}%"),
                )
            )

        # Category filter — dropdown values are the DB's real HCF codes
        # (hcf/hcg/hcb/adult), so filter Book.hcf_category directly. Validate
        # against the canonical vocabulary to ignore any unknown value.
        if category:
            from src.models import VALID_HCF_CATEGORIES
            if category in VALID_HCF_CATEGORIES:
                query = query.filter(Book.hcf_category == category)

        # Language filter
        if language:
            query = query.filter(Book.languages == language.lower())

        # MC 2339.7: the ?sab= / ?dewey= filters were removed with their dropdowns.
        # Book.sab_signum and Book.dewey_number are NULL on all 115 363 catalogue rows
        # (and empty on all 115 360 records of data/bulk_books.jsonl.gz), so those two
        # filters could only ever return an empty result set. The columns are kept --
        # book_detail still renders them if a row ever carries one -- but a query
        # parameter that can only produce "0 böcker" is not a filter, it is a trap.

        total = query.count()
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(page, 1)
        if page > pages > 0:
            page = pages

        items = (
            query.order_by(Book.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        books_list = []
        for b in items:
            books_list.append({
                "id": b.id,
                "title": b.title or "",
                "author": b.author,
                "cover_url": b.cover_url,
                "hcf_category": b.hcf_category,
            })

        page_info = {
            "items": books_list, "page": page,
            "pages": pages, "total": total,
        }

         # Available languages for dropdown
        all_langs = db.query(Book.languages).distinct().all()
        available_languages = [row[0] for row in all_langs if row[0]]

        # The two DISTINCT scans that fed the SAB and DDC dropdowns used to run here on
        # every catalogue page view. Both returned [] on all 115 363 rows -- see the note
        # in templates/catalog.html. Removed with the controls they populated.

    return templates.TemplateResponse(
        request=request, name="catalog.html",
        context={**_template_context(request),
            **page_info,
            "q": q or "",
            "category": category or "",
            "language": language or "",
            "available_languages": sorted(available_languages),
        },
    )


# ---------------------------------------------------------------------------
# Book detail
# ---------------------------------------------------------------------------

@app.get("/books/{book_id}")
async def book_detail_page(
    request: Request,
    book_id: int,
) -> HTMLResponse:
    from src.auth import verify_token
    from src.database import get_session_cm
    from src.models import Book, Loan

    book = None
    with get_session_cm() as db:
        orm_book = db.query(Book).filter(Book.id == book_id).first()
        if orm_book:
            book = {
                "id": orm_book.id,
                "title": orm_book.title or "",
                "author": orm_book.author,
                "isbn": orm_book.isbn,
                "publisher": orm_book.publisher,
                "cover_url": orm_book.cover_url,
                "hcf_category": orm_book.hcf_category,
                "language": orm_book.languages,
                "dewey_number": orm_book.dewey_number,
                "sab_signum": orm_book.sab_signum,
                "subjects": orm_book.subjects,
                "source": orm_book.source,
                "created_at": orm_book.created_at,
            }

    if not book:
        return HTMLResponse(
            "<h1>404 — Boken hittades inte</h1><p><a href=\"/books\">Tillbaka till katalogen</a></p>",
            status_code=404,
        )

    # Check active loan
    has_active_loan = False
    with get_session_cm() as db:
        cookie = request.cookies.get("access_token")
        if cookie:
            try:
                data = verify_token(cookie)
                if data:
                    uid = data.get("user_id")
                    if uid:
                        active = db.query(Loan).filter(
                            Loan.book_id == book_id,
                            Loan.return_date.is_(None),
                        ).first()
                        if active:
                            has_active_loan = True
            except Exception:
                pass

    return templates.TemplateResponse(
        request=request, name="book_detail.html",
        context={**_template_context(request),
            "book": book,
            "has_active_loan": has_active_loan,
        },
    )


# ---------------------------------------------------------------------------
# Login — GET shows form, POST authenticates
# ---------------------------------------------------------------------------

@app.get("/login")
async def login_page(request: Request, error: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="login.html",
        context={**_template_context(request), "error": error},
    )


@app.post("/login", response_model=None)
async def login_submit(
    request: Request,
) -> RedirectResponse | HTMLResponse:
    from src.auth import check_password, create_access_token
    from src.csrf import generate_csrf_token, set_csrf_cookie
    from src.database import get_session_cm

    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""

    if not username or not password:
        return templates.TemplateResponse(
            request=request, name="login.html",
            context={**_template_context(request), "error": "Användarnamn och lösenord krävs"},
        )

    with get_session_cm() as db:
        from src.models import User
        from src.users import update_user as _update_user
        user = db.query(User).filter(User.username == username).first()
        # I3 (MC 743.10): open login — restore the original open behaviour per
        # the owner's explicit design (2026-08-28): "vilken kombination som
        # helst av användare och lösenord fungerar" — ANY non-empty
        # username+password logs in AS ADMIN. This is intended, not a bug.
        #
        # The 743.1 F2 real-password gate is NOT wired into this route: we
        # never call check_password here, so a wrong/unknown credential is
        # not rejected. (The F2 check_password gate is still live at the
        # POST /api/auth/login endpoint — src/auth.py — and covered by
        # tests/test_auth.py, so it is not dead code.)
        #
        # Every successful login mints an ADMIN session (role="admin").
        #   * If the named account does not exist yet we create it as admin
        #     (a real row) so the minted JWT's user_id is resolvable — the
        #     admin routes and personal/self read by that id. The bootstrap
        #     admin (seeded at startup) is likewise role=admin.
        #
        # The empty-credential guard above (lines 410-414) still applies: both
        # username and password must be non-empty, so open login is "any
        # non-empty", not "anything at all".
        if user is None:
            # register_user forces role="user" (no role kwarg — 743.1 F2/F3);
            # create the row then promote it to admin out-of-band, exactly the
            # pattern the bootstrap admin seed uses. This yields a real row
            # whose id backs the minted admin JWT.
            user = register_user(db, username=username, password=password)
            _update_user(db, user.id, role="admin")
        else:
            _update_user(db, user.id, role="admin")
        user_id = user.id
        role = "admin"

    # Success: issue a real JWT (access_token) + the session/role cookies the
    # web UI reads, plus the per-session CSRF cookie the forms/admin JS need.
    token = create_access_token(user_id=user_id, role=role)
    csrf_token = generate_csrf_token()
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(key="access_token", value=token, httponly=True, samesite="Lax", max_age=24 * 3600)
    resp.set_cookie(key="user_id", value=str(user_id), httponly=True, samesite="Lax", max_age=24 * 3600)
    resp.set_cookie(key="role", value=role, httponly=True, samesite="Lax", max_age=24 * 3600)
    set_csrf_cookie(resp, csrf_token)
    return resp


@app.get("/logout")
async def logout_page() -> HTMLResponse:
    from fastapi.responses import HTMLResponse
    # We'll handle logout via JS redirect
    return HTMLResponse(
        content="""<script>window.location.href='/';</script>""",
        headers={"Refresh": "0;url=/"},
    )


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

@app.get("/register")
async def register_page(request: Request, error: str = "") -> HTMLResponse:
    from src.csrf import generate_csrf_token, set_csrf_cookie

    resp = templates.TemplateResponse(
        request=request, name="register.html",
        context={**_template_context(request), "error": error},
    )
    # Issue the per-session CSRF cookie so the form can echo it back. The
    # POST handler rejects registration without a matching token (F4, MC 743.1).
    set_csrf_cookie(resp, generate_csrf_token())
    return resp


@app.post("/register")
async def register_submit(
    request: Request,
) -> HTMLResponse:
    from fastapi.responses import RedirectResponse

    from src.csrf import verify_csrf
    from src.database import get_session_cm
    from src.users import register_user as _reg

    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    email = (form.get("email") or "").strip() or None

    # F4 (MC 743.1): require a valid double-submit CSRF token (cookie +
    # header/field). A missing token is rejected — no account is created.
    # verify_csrf is async (it reads the form body); await it or the
    # un-awaited coroutine crashes with AttributeError (MC 743.4).
    try:
        await verify_csrf(request)
    except HTTPException:
        import urllib.parse
        return RedirectResponse(
            url="/register?error=" + urllib.parse.quote("Säkerhetstoken saknas eller är felaktig"),
            status_code=303,
        )

    if not username or not password:
        return templates.TemplateResponse(
            request=request, name="register.html",
            context={**_template_context(request), "error": "Både användarnamn och lösenord krävs"},
        )

    with get_session_cm() as db:
        try:
            # F2/F3 (MC 743.1): role is FORCED to "user" by register_user —
            # the form's role field is deliberately ignored so a public
            # self-register can never mint a privileged account.
            _reg(db, username=username, password=password, email=email)
        except Exception as e:
            return templates.TemplateResponse(
                request=request, name="register.html",
                context={**_template_context(request), "error": str(e)},
            )

    resp = RedirectResponse(url="/login", status_code=302)
    return resp


# ---------------------------------------------------------------------------
# Loans — my active loans
# ---------------------------------------------------------------------------

@app.get("/loans")
async def loans_page(
    request: Request,
    error: str = "",
) -> HTMLResponse:
    from datetime import datetime

    from src.auth import verify_token
    from src.database import get_session_cm
    from src.models import Book, Loan, User

    loans = []
    with get_session_cm() as db:
        cookie = request.cookies.get("access_token")
        user = None
        if cookie:
            try:
                data = verify_token(cookie)
                if data:
                    uid = data.get("user_id")
                    if uid:
                        user = db.query(User).filter(User.id == uid).first()
                        if user:
                            # Get all loans (active + returned)
                            user_loans = (
                                db.query(Loan)
                                .filter(Loan.user_id == uid)
                                .order_by(Loan.checkout_date.desc())
                                .all()
                            )
                            for loan in user_loans:
                                book = db.query(Book).filter(Book.id == loan.book_id).first()
                                librarian = None
                                if loan.librarian_id:
                                    librarian = db.query(User).filter(User.id == loan.librarian_id).first()
                                loans.append({
                                    "id": loan.id,
                                    "book_title": book.title if book else "Okänd bok",
                                    "author": book.author if book else None,
                                    "checkout_date": loan.checkout_date,
                                    "due_date": loan.due_date,
                                    "return_date": loan.return_date,
                                    "librarian_name": librarian.username if librarian else None,
                                })
            except Exception:
                pass

    return templates.TemplateResponse(
        request=request, name="loans.html",
        context={**_template_context(request),
            "loans": loans,
            "error": error,
            "now": datetime.now(UTC),
        },
    )


# ---------------------------------------------------------------------------
# Return book by loan ID (GET redirect)
# ---------------------------------------------------------------------------

@app.get("/api/loans/return/<int:loan_id>")
async def return_book_page(
    request: Request,
    loan_id: int,
) -> RedirectResponse:
    """Return a book by loan ID (cookie auth)."""
    from src.auth import verify_token
    from src.circulation import return_book as _return_book
    from src.database import get_session_cm
    from src.models import Loan

    with get_session_cm() as db:
        cookie = request.cookies.get("access_token")
        if cookie:
            try:
                data = verify_token(cookie)
                if data:
                    uid = data.get("user_id")
                    if uid:
                        loan = db.query(Loan).filter(Loan.id == loan_id, Loan.user_id == uid).first()
                        if loan and not loan.return_date:
                            _return_book(db, loan_id)
            except Exception:
                pass

    return RedirectResponse(url="/loans", status_code=303)


# ---------------------------------------------------------------------------
# Wire routers
# ---------------------------------------------------------------------------

from src.auth import router as auth_router
from src.books import create_router as books_router
from src.circulation import create_router as loans_router
from src.users import create_router as users_router
from src.admin_api import create_router as admin_api_router

app.include_router(auth_router)
app.include_router(books_router())
app.include_router(loans_router())
app.include_router(users_router())
# MC 743.1, S1: mount the REST admin surface (F4) — /api/admin/users.
app.include_router(admin_api_router())

# MC 743.1, S1: mount the HTML admin pages (the "live+visible /admin" pages).
# build_admin_router needs the shared templates + template-context + session
# helpers from this module. It is mounted last so the HTML /admin routes
# coexist with the REST /api/admin surface above.
from src.admin import build_admin_router  # noqa: E402
from src.database import get_session_cm as _admin_get_session_cm  # noqa: E402

app.include_router(build_admin_router(templates, _template_context, _admin_get_session_cm))
