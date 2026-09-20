"""Bibliotek — Book CRUD + Swedish Libris XSearch integration + FastAPI router.

Plain functions accept a DB session (``sqlalchemy.orm.Session``) as their first
argument so that callers can control lifecycle explicitly.  A standalone
``Book`` dataclass is provided for use when the ORM is not yet available.

Exports
-------
libris_search · libris_lookup_by_isbn · create_book · get_book
list_books · scan_barcode · classify_hcf · create_router
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth import require_role
from src.database import get_engine
from src.models import Book as BookModel

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI session dependency — yields a Session, auto-commits/rollbacks
# ---------------------------------------------------------------------------


def _iter_session():
    """Yield a SQLAlchemy session; auto-commits/rollbacks on exception.

    NOTE: plain generator (no @contextmanager) — used via `yield from` in
    the FastAPI dependency ``get_db()`` which wraps it in its own cleanup.
    """
    session = Session(get_engine())
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    """FastAPI dependency: yields a DB session with commit/rollback."""
    yield from _iter_session()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LIBRIS_BASE = "https://libris.kb.se/api/xsearch"
LIBRIS_TIMEOUT = 10.0
HCF_CATEGORIES = ("hcf", "hcg", "hcb", "adult")

# ---------------------------------------------------------------------------
# Portable dataclass (works without ORM)
# ---------------------------------------------------------------------------


@dataclass
class Book:
    """Portable book record used for API serialisation."""

    id: int | None = None
    isbn: str | None = None
    title: str = ""
    author: str | None = None
    publisher: str | None = None
    year: int | None = None
    cover_url: str | None = None
    hcf_category: str | None = None
    dewey_number: str | None = None
    subjects: str | None = None
    languages: str | None = None
    source: str | None = None
    created_at: datetime | None = None
    created_by: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict suitable for JSON responses."""
        d = asdict(self)
        if isinstance(d.get("created_at"), datetime):
            d["created_at"] = d["created_at"].isoformat()
        return d


# ---------------------------------------------------------------------------
# Pydantic schemas for API request validation
# ---------------------------------------------------------------------------


class BookCreate(BaseModel):
    """Schema for the POST /api/books request body."""

    isbn: str = Field(..., description="ISBN-13 / EAN-13 string")
    title: str = Field(..., description="Book title")
    author: str | None = Field(None, description="Author name")
    publisher: str | None = Field(None, description="Publisher name")
    year: int | None = Field(None, description="Publication year")
    hcf_category: str | None = Field(
        None,
        description=(
            "One of ``hcf``, ``hcg``, ``hcb``, ``adult``, or ``None``."
        ),
    )
    user_id: int | None = Field(None, description="ID of the creating user")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _orm_to_book(row: BookModel) -> Book:
    """Convert an ORM Book instance to our portable Book dataclass."""
    return Book(
        id=row.id,
        isbn=row.isbn,
        title=row.title,
        author=row.author,
        publisher=row.publisher,
        year=row.year,
        cover_url=row.cover_url,
        hcf_category=row.hcf_category,
        dewey_number=getattr(row, 'dewey_number', None),
        subjects=getattr(row, 'subjects', None),
        languages=getattr(row, 'languages', None),
        source=getattr(row, 'source', None),
        created_at=row.created_at,
        created_by=row.created_by,
    )


def _extract_year(date_str: str | None) -> int | None:
    """Pull a 4-digit year from a Libris date string."""
    if not date_str:
        return None
    for i in range(len(date_str) - 3):
        chunk = date_str[i: i + 4]
        if chunk.isdigit() and chunk[0] in ("1", "2"):
            return int(chunk)
    return None


def _first(val: str | list | None):
    """Return the first non-empty string from *val*, or None."""
    if isinstance(val, list):
        return next((v for v in val if v), None)
    return val


# ---------------------------------------------------------------------------
# Libris XSearch helpers
# ---------------------------------------------------------------------------


def libris_search(query: str, limit: int = 10) -> list[dict]:
    """Search the Swedish Libris catalog via the XSearch API.

    Parameters
    ----------
    query:
        Free-text query (or bare ISBN digits).
    limit:
        Max results to return (default 10).

    Returns
    -------
    list[dict]
        Raw records from Libris.
    """
    params: dict[str, Any] = {"q": query, "format": "json", "limit": limit}
    try:
        resp = httpx.get(LIBRIS_BASE, params=params, timeout=LIBRIS_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("Libris XSearch failed for q=%r: %s", query, exc)
        return []

    data = resp.json()
    return data.get("xsearch", {}).get("list", [])


def libris_lookup_by_isbn(isbn: str) -> dict | None:
    """Look up a single book by ISBN via Libris.

    The bare ISBN digits are used as the query (Libris XSearch does not
    support the ``isbn:`` prefix).

    Returns
    -------
    dict | None
        Parsed record if found, else None.
    """
    clean_isbn = isbn.strip().replace("-", "").replace(" ", "")
    results = libris_search(clean_isbn, limit=1)
    if results:
        return _parse_libris_record(results[0])
    return None


def _parse_libris_record(record: dict) -> dict:
    """Normalise a raw Libris XSearch record to a stable shape."""
    return {
        "title": _first(record.get("title", "")),
        "author": _first(record.get("creator")),
        "publisher": _first(record.get("publisher")),
        "year": _extract_year(_first(record.get("date"))),
        "cover_url": None,
        "libris_id": record.get("identifier"),
        "isbn": _first(record.get("isbn")),
        "language": _first(record.get("language")),
        "type": _first(record.get("type")),
    }


# ---------------------------------------------------------------------------
# HCF classification heuristic
# ---------------------------------------------------------------------------


def classify_hcf(
    title: str, author: str | None = None, year: int | None = None
) -> str | None:
    """Heuristic HCF category classifier.

    Returns one of ``hcf``, ``hcg``, ``hcb``, ``adult``, or ``None`` when
    the classifier is unsure.
    """
    title_lower = (title or "").lower()
    author_lower = (author or "").lower()
    combined = f" {title_lower} {author_lower} "

    hcf_kw = ["barnbok", "barn", "små", "lese", "lilla", "babies",
              "preschool", "förskola", "tidigläsare"]
    hcg_kw = ["ungdomsbok", "ungdom", "unga", "middle grade",
              "10-12", "caprice junior"]
    hcb_kw = ["tonårsbok", "tonår", "young adult", "ya", "hcb",
              "huvudkategoriför 13", "adult fiction"]

    scores: dict[str, int] = {
        "hcf": sum(1 for kw in hcf_kw if kw in combined),
        "hcg": sum(1 for kw in hcg_kw if kw in combined),
        "hcb": sum(1 for kw in hcb_kw if kw in combined),
    }

    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else None


# ---------------------------------------------------------------------------
# CRUD functions — accept explicit DB session
# ---------------------------------------------------------------------------


def create_book(
    db: Session,
    isbn: str,
    title: str,
    author: str | None = None,
    publisher: str | None = None,
    year: int | None = None,
    hcf_category: str | None = None,
    user_id: int | None = None,
) -> Book:
    """Persist a new book in the local database.

    Parameters
    ----------
    db:
        SQLAlchemy session (caller owns lifecycle).
    isbn:
        ISBN-13 / EAN-13 string.
    title:
        Book title.
    author:
        Author name (optional).
    publisher:
        Publisher name (optional).
    year:
        Publication year (optional).
    hcf_category:
        One of ``hcf``, ``hcg``, ``hcb``, ``adult``, or ``None``.
        If ``None`` the heuristic classifier is applied.
    user_id:
        ID of the user creating the book (optional).

    Returns
    -------
    Book
        The newly created book (portable dataclass).

    Raises
    ------
    HTTPException(400):
        If *isbn* already exists in the database.
    """
    if hcf_category is None:
        hcf_category = classify_hcf(title, author, year)
        if hcf_category is not None and hcf_category not in HCF_CATEGORIES:
            hcf_category = None

    existing = (
        db.query(BookModel)
        .filter(BookModel.isbn == isbn)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A book with this ISBN already exists",
        )

    book = BookModel(
        isbn=isbn,
        title=title,
        author=author,
        publisher=publisher,
        year=year,
        hcf_category=hcf_category,
        created_by=user_id,
    )
    db.add(book)
    db.flush()
    db.refresh(book)

    return _orm_to_book(book)


def get_book(db: Session, book_id: int) -> Book:
    """Retrieve a single book by primary key.

    Raises
    ------
    HTTPException(404):
        When no book with *book_id* exists.
    """
    row = db.get(BookModel, book_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Book not found",
        )
    return _orm_to_book(row)


def list_books(
    db: Session,
    limit: int = 20,
    offset: int = 0,
    hcf_category: str | None = None,
) -> tuple[list[Book], int]:
    """Return a paginated list of books.

    Returns
    -------
    tuple[list[Book], int]
        ``(items, total_count)``.
    """
    q = db.query(BookModel)
    if hcf_category:
        q = q.filter(BookModel.hcf_category == hcf_category)

    total = q.count()
    rows = (
        q.order_by(BookModel.id)
        .offset(offset)
        .limit(limit)
        .all()
    )

    books = [_orm_to_book(r) for r in rows]
    return books, total


def scan_barcode(db: Session, barcode: str) -> Book | None:
    """Scan a barcode (EAN-13 / ISBN) and return a Book.

    Strategy
    --------
    1. Try a local DB lookup by exact ISBN match.
    2. If not found locally, query Libris XSearch and create a new
       book record from the returned data.
    """
    clean = barcode.strip().replace("-", "").replace(" ", "")

    # 1. Local lookup by ISBN
    row = (
        db.query(BookModel)
        .filter(BookModel.isbn == clean)
        .first()
    )
    if row:
        return _orm_to_book(row)

    # 2. Libris search fallback
    results = libris_search(clean, limit=5)
    if results:
        parsed = _parse_libris_record(results[0])
        return Book(
            isbn=parsed.get("isbn"),
            title=parsed["title"] or "Unknown",
            author=parsed.get("author"),
            publisher=parsed.get("publisher"),
            year=parsed.get("year"),
            hcf_category=classify_hcf(
                parsed.get("title", ""),
                parsed.get("author"),
                parsed.get("year"),
            ),
        )
    return None


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------


def create_router() -> APIRouter:
    """Build and return a FastAPI ``APIRouter`` mounted at ``/api/books``.

    Returns
    -------
    APIRouter
        A configured router ready to be ``app.include_router(router)``-ed
        into the main application.
    """
    router = APIRouter(prefix="/api/books", tags=["books"])

    @router.get("", response_model=dict)
    def list_books_endpoint(
        db: Session = Depends(get_db),
        limit: int = Query(20, ge=1, le=200),
        offset: int = Query(0, ge=0),
        hcf_category: str | None = Query(None),
    ) -> dict:
        """GET /api/books — paginated book list."""
        items, total = list_books(db, limit=limit, offset=offset,
                                   hcf_category=hcf_category)
        return {
            "items": [b.to_dict() for b in items],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @router.get("/search", response_model=dict)
    def search_books_endpoint(
        q: str = Query(..., min_length=1),
        db: Session = Depends(get_db),
    ) -> dict:
        """GET /api/books/search?q=<query> — search local DB."""
        terms = f"%{q}%"
        rows = (
            db.query(BookModel)
            .filter(
                (BookModel.title.ilike(terms))
                | (BookModel.author.ilike(terms))
                | (BookModel.isbn.ilike(terms))
            )
            .limit(20)
            .all()
        )
        items = [_orm_to_book(r) for r in rows]
        total = len(items)

        return {
            "items": [b.to_dict() for b in items],
            "total": total,
            "limit": 20,
            "offset": 0,
        }

    @router.get("/{book_id}", response_model=dict)
    def get_book_endpoint(
        book_id: int,
        db: Session = Depends(get_db),
        current_user: dict = Depends(require_role(["admin", "librarian", "user"])),
    ) -> dict:
        """GET /api/books/{id} — single book by primary key.

        A-06 (MC 1267): the role list contained "reader", which is not a
        valid role, so every plain user got 403 here while the list
        endpoint is public. Plain users may read a book detail.
        """
        book = get_book(db, book_id)
        return book.to_dict()

    @router.post("", response_model=dict)
    async def create_book_endpoint(
        body: BookCreate,
        db: Session = Depends(get_db),
        current_user: dict = Depends(require_role(["admin", "librarian"])),
    ) -> dict:
        """POST /api/books — add a new book.

        A-05 (MC 1267): this endpoint had no auth dependency at all — any
        anonymous client could insert catalog rows. Catalog writes are a
        staff action: admin/librarian Bearer required (matching the app's
        own model for user/staff management).
        """
        book = create_book(
            db,
            isbn=body.isbn,
            title=body.title,
            author=body.author,
            publisher=body.publisher,
            year=body.year,
            hcf_category=body.hcf_category,
            user_id=body.user_id,
        )
        return book.to_dict()

    @router.post("/scan", response_model=dict)
    def scan_barcode_endpoint(
        barcode: str = Query(..., min_length=1),
        db: Session = Depends(get_db),
    ) -> dict:
        """POST /api/books/scan?barcode=<ean13> — scan barcode → Libris."""
        result = scan_barcode(db, barcode)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No book found for barcode",
            )
        return result.to_dict()

    return router
