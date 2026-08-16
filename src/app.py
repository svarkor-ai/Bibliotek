"""Bibliotek — FastAPI application entry point.

Wires together all feature routers, sets up Jinja2 templates,
mounts static files, and runs startup initialisation (DB creation
+ admin seed user).
"""


from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.database import init_db
from src.models import User
from src.users import register_user

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
    """Initialise the database and ensure an admin account exists."""
    init_db()

    from src.database import get_session_cm

    with get_session_cm() as db:
        admin = db.query(User).filter(User.username == "admin").first()
        if admin is None:
            register_user(
                db,
                username="admin",
                password="admin",
                role="admin",
            )


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

def _page_info(page: int, total: int, per_page: int) -> dict:
    pages = max(1, (total + per_page - 1) // per_page)
    return {"items": [], "page": page, "pages": pages, "total": total}


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
        context={**_template_context(request), "recent_books": recent, "popular_categories": categories},
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
    sab: str = "",
    dewey: str = "",
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

        # Category filter
        if category:
            cat_map = {"barn": "barn", "unga": "unga", "vuxen": "vuxen"}
            if category in cat_map:
                query = query.filter(Book.hcf_category == cat_map[category])

        # Language filter
        if language:
            query = query.filter(Book.languages == language.lower())

        # SAB filter
        if sab:
            query = query.filter(Book.sab_signum == sab)

        # Dewey filter
        if dewey:
            query = query.filter(Book.dewey_number == dewey)

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

        # Available SAB classes for dropdown
        sab_classes = db.query(Book.sab_signum).distinct().order_by(Book.sab_signum).all()
        available_sab = [row[0] for row in sab_classes if row[0]]

        # Available Dewey numbers for dropdown
        dewey_numbers = db.query(Book.dewey_number).distinct().order_by(Book.dewey_number).all()
        available_dewey = [row[0] for row in dewey_numbers if row[0]]

    return templates.TemplateResponse(
        request=request, name="catalog.html",
        context={**_template_context(request),
            **page_info,
            "q": q or "",
            "category": category or "",
            "language": language or "",
            "sab": sab or "",
            "dewey": dewey or "",
            "available_languages": sorted(available_languages),
            "available_sab": available_sab,
            "available_dewey": available_dewey,
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
    from src.auth import create_access_token
    from src.database import get_session_cm

    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    # For this prototype/POC: any non-empty login grants admin access
    if username and password:
        with get_session_cm() as db:
            from src.models import User
            user = db.query(User).filter(User.username == "admin").first()
            if user is None:
                from src.users import register_user
                user = register_user(db, username="admin", password="admin", role="admin")
            token = create_access_token(user_id=user.id, role=user.role)
            resp = RedirectResponse(url="/", status_code=303)
            resp.set_cookie(key="access_token", value=token)
            return resp

    return templates.TemplateResponse(
        request=request, name="login.html",
        context={**_template_context(request), "error": "Användarnamn och lösenord krävs"},
    )


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
    return templates.TemplateResponse(
        request=request, name="register.html",
        context={**_template_context(request), "error": error},
    )


@app.post("/register")
async def register_submit(
    request: Request,
) -> HTMLResponse:
    from fastapi.responses import RedirectResponse

    from src.database import get_session_cm
    from src.users import register_user as _reg

    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    role = form.get("role", "user")

    if not username or not password:
        return templates.TemplateResponse(
            request=request, name="register.html",
            context={**_template_context(request), "error": "Både användarnamn och lösenord krävs"},
        )

    with get_session_cm() as db:
        try:
            _reg(db, username=username, password=password, role=role)
        except Exception as e:
            return templates.TemplateResponse(
                request=request, name="register.html",
                context={**_template_context(request), "error": str(e)},
            )

    resp = RedirectResponse(url="/login", status_code=303)
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

app.include_router(auth_router)
app.include_router(books_router())
app.include_router(loans_router())
app.include_router(users_router())
