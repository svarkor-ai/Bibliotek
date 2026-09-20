"""Circulation module — checkout / return / overdue logic for Bibliotek.

28-day hard lending period.

Functions
    checkout(db, book_id, user_id, librarian_id) → Loan
    return_book(db, loan_id) → Loan
    is_overdue(loan) → bool
    get_user_loans(db, user_id, active_only=True) → list[Loan]
    get_overdue_loans(db) → list[Loan]
    get_book_loans(db, book_id, active_only=True) → list[Loan]
    create_router() → FastAPI router

Endpoints
    POST /api/loans/checkout   (book_id, user_id, librarian_id) → {loan}
    POST /api/loans/return     (loan_id) → {loan}
    GET  /api/loans/active     → [loans]
    GET  /api/loans/overdue    → [loans]  [admin+librarian]
    GET  /api/loans/user/{id}  → [loans]
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.auth import require_role
from src.database import get_session
from src.models import Book, Loan, User

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHECKOUT = timedelta(days=28)  # hard 28-day lending period

# ---------------------------------------------------------------------------
# Core business logic
# ---------------------------------------------------------------------------


def checkout(
    db: Session,
    book_id: int,
    user_id: int,
    librarian_id: int,
) -> Loan:
    """Check out a book.

    Creates a new Loan with::

        due_date = checkout_date (now) + 28 days

    Raises HTTPException 400 if the book already has an active loan.
    Raises HTTPException 404 if any referenced entity is missing.
    """
    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")

    borrower = db.query(User).filter(User.id == user_id).first()
    if borrower is None:
        raise HTTPException(status_code=404, detail="User not found")

    librarian = db.query(User).filter(User.id == librarian_id).first()
    if librarian is None:
        raise HTTPException(status_code=404, detail="Librarian not found")

    # Book must not have an active (unreturned) loan
    active = (
        db.query(Loan)
        .filter(Loan.book_id == book_id, Loan.return_date.is_(None))
        .first()
    )
    if active is not None:
        raise HTTPException(
            status_code=400,
            detail="Book is already checked out",
        )

    now = datetime.now(UTC)
    loan = Loan(
        book_id=book_id,
        user_id=user_id,
        librarian_id=librarian_id,
        checkout_date=now,
        due_date=now + CHECKOUT,
        return_date=None,
    )
    db.add(loan)
    db.commit()
    db.refresh(loan)
    return loan


def return_book(db: Session, loan_id: int) -> Loan:
    """Return a book by setting its return_date.

    Raises HTTPException 404 if the loan does not exist or is already returned.
    """
    loan = db.query(Loan).filter(Loan.id == loan_id).first()
    if loan is None:
        raise HTTPException(status_code=404, detail="Loan not found")
    if loan.return_date is not None:
        raise HTTPException(
            status_code=400,
            detail="Book already returned",
        )

    loan.return_date = datetime.now(UTC)
    db.commit()
    db.refresh(loan)
    return loan


def is_overdue(loan: Loan) -> bool:
    """Return True if the loan is overdue: active (no return) and past due_date."""
    if loan.return_date is not None:
        return False
    now = datetime.now(UTC)
    due = loan.due_date
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return now > due


def get_user_loans(
    db: Session,
    user_id: int,
    active_only: bool = True,
) -> list[Loan]:
    """Return loans for *user_id* as borrower.

    If *active_only* is True, only loans with return_date is None.
    """
    q = db.query(Loan).filter(Loan.user_id == user_id)
    if active_only:
        q = q.filter(Loan.return_date.is_(None))
    return q.order_by(Loan.checkout_date.desc()).all()


def get_overdue_loans(db: Session) -> list[Loan]:
    """Return all active (unreturned) loans whose due_date has passed."""
    now = datetime.now(UTC)
    return (
        db.query(Loan)
        .filter(Loan.return_date.is_(None), Loan.due_date < now)
        .order_by(Loan.due_date.asc())
        .all()
    )


def get_book_loans(
    db: Session,
    book_id: int,
    active_only: bool = True,
) -> list[Loan]:
    """Return all loans for *book_id*.

    If *active_only* is True, only loans with return_date is None.
    """
    q = db.query(Loan).filter(Loan.book_id == book_id)
    if active_only:
        q = q.filter(Loan.return_date.is_(None))
    return q.order_by(Loan.checkout_date.desc()).all()


# ---------------------------------------------------------------------------
# Pydantic schema helpers (inline — no extra file needed)
# ---------------------------------------------------------------------------

from pydantic import BaseModel


class _LoanOut(BaseModel):
    """Compact loan representation for JSON responses."""

    id: int
    book_id: int
    user_id: int
    librarian_id: int
    checkout_date: datetime
    due_date: datetime
    return_date: datetime | None
    overdue: bool

    model_config = {"from_attributes": True}


def _loan_to_dict(loan: Loan) -> dict[str, Any]:
    """Serialise a Loan ORM instance to a plain dict."""
    return {
        "id": loan.id,
        "book_id": loan.book_id,
        "user_id": loan.user_id,
        "librarian_id": loan.librarian_id,
        "checkout_date": loan.checkout_date,
        "due_date": loan.due_date,
        "return_date": loan.return_date,
        "overdue": is_overdue(loan),
    }


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------


def create_router() -> APIRouter:
    """Create and configure the circulation API router."""
    router = APIRouter(prefix="/api/loans", tags=["loans"])

    # -- helpers used inside endpoint closures --
    _dep_active = require_role(["admin", "librarian", "user"])
    _dep_admin = require_role(["admin", "librarian"])

    # ------------------------------------------------------------------
    # POST /api/loans/checkout
    # ------------------------------------------------------------------
    @router.post("/checkout")
    async def loan_checkout(
        body: dict = Body(...),
        current_user: Any = Depends(_dep_active),
        db: Session = Depends(get_session),
    ) -> dict:
        """Check out a book."""
        book_id = body.get("book_id")
        user_id = body.get("user_id")
        librarian_id = body.get("librarian_id")

        if book_id is None or user_id is None or librarian_id is None:
            raise HTTPException(
                status_code=400,
                detail="book_id, user_id, librarian_id are required",
            )

        # A-07 (MC 1267): object-level authz — a plain user may only charge
        # the loan to themselves. Admin/librarian keep the explicit choice
        # (they check out on behalf of a borrower at the desk).
        if current_user["role"] not in ("admin", "librarian"):
            user_id = current_user["user_id"]

        loan = checkout(db, book_id, user_id, librarian_id)
        return _loan_to_dict(loan)

    # ------------------------------------------------------------------
    # POST /api/loans/return
    # ------------------------------------------------------------------
    @router.post("/return")
    async def loan_return(
        body: dict = Body(...),
        current_user: Any = Depends(_dep_admin),
        db: Session = Depends(get_session),
    ) -> dict:
        """Return a book."""
        loan_id = body.get("loan_id")
        if loan_id is None:
            raise HTTPException(
                status_code=400,
                detail="loan_id is required",
            )

        loan = return_book(db, loan_id)
        return _loan_to_dict(loan)

    # ------------------------------------------------------------------
    # GET /api/loans/active
    # ------------------------------------------------------------------
    @router.get("/active")
    async def list_active(
        current_user: Any = Depends(_dep_active),
        db: Session = Depends(get_session),
    ) -> list[dict]:
        """All active (unreturned) loans with overdue flag.

        N1 (MC 1267): admin/librarian see ALL active loans (per the module
        docstring + DESIGN.md); plain users see only their own.
        """
        if current_user["role"] in ("admin", "librarian"):
            loans = db.query(Loan).filter(Loan.return_date.is_(None)).all()
        else:
            loans = get_user_loans(db, current_user["user_id"], active_only=True)
        return [_loan_to_dict(l) for l in loans]

    # ------------------------------------------------------------------
    # GET /api/loans/overdue
    # ------------------------------------------------------------------
    @router.get("/overdue")
    async def list_overdue(
        current_user: Any = Depends(_dep_admin),
        db: Session = Depends(get_session),
    ) -> list[dict]:
        """All overdue active loans (admin/librarian only)."""
        loans = get_overdue_loans(db)
        return [_loan_to_dict(l) for l in loans]

    # ------------------------------------------------------------------
    # GET  /api/loans/user/{id}  → [loans]
    # ------------------------------------------------------------------
    @router.get("/user/{user_id}")
    async def list_user_loans(
        user_id: int,
        current_user: Any = Depends(_dep_active),
        db: Session = Depends(get_session),
    ) -> list[dict]:
        """Loan history for a specific user."""
        if (
            current_user["role"] == "user"
            and current_user["user_id"] != user_id
        ):
            raise HTTPException(
                status_code=403,
                detail="Cannot view another user's loans",
            )
        loans = get_user_loans(db, user_id, active_only=False)
        return [_loan_to_dict(l) for l in loans]

    # ------------------------------------------------------------------
    # POST /api/loans/checkout-cookie  (prototype mode — uses cookie auth)
    # ------------------------------------------------------------------
    @router.post("/checkout-cookie")
    async def loan_checkout_cookie(
        request: Request,
        db: Session = Depends(get_session),
    ) -> dict:
        """Check out a book using cookie auth (prototype-friendly)."""
        from src.auth import verify_token
        from src.csrf import verify_csrf

        # A-08 (MC 1267): the cookie flow is browser-facing, so it carries
        # the same double-submit CSRF check as the admin surface.
        await verify_csrf(request)

        body = await request.json()
        book_id = body.get("book_id")
        if book_id is None:
            raise HTTPException(status_code=400, detail="book_id is required")

        cookie = request.cookies.get("access_token")
        if not cookie:
            raise HTTPException(status_code=401, detail="Not logged in")

        data = verify_token(cookie)
        if not data:
            raise HTTPException(status_code=401, detail="Invalid token")

        user_id = data.get("user_id")
        if not user_id:
            raise HTTPException(status_code=401, detail="No user_id in token")

        loan = checkout(db, book_id, user_id, user_id)
        return _loan_to_dict(loan)

    # ------------------------------------------------------------------
    # POST /api/loans/return-cookie  (prototype mode — uses cookie auth)
    # ------------------------------------------------------------------
    @router.post("/return-cookie")
    async def loan_return_cookie(
        request: Request,
        db: Session = Depends(get_session),
    ) -> dict:
        """Return a book using cookie auth (prototype-friendly)."""
        from src.auth import verify_token
        from src.csrf import verify_csrf

        # A-09 (MC 1267): CSRF check (same as checkout-cookie) AND an
        # object-level ownership check — a plain user may only return
        # their own loan; librarian/admin may return any loan.
        await verify_csrf(request)

        body = await request.json()
        loan_id = body.get("loan_id")
        if loan_id is None:
            raise HTTPException(status_code=400, detail="loan_id is required")

        cookie = request.cookies.get("access_token")
        if not cookie:
            raise HTTPException(status_code=401, detail="Not logged in")

        data = verify_token(cookie)
        if not data:
            raise HTTPException(status_code=401, detail="Invalid token")

        user_id = data.get("user_id")
        if not user_id:
            raise HTTPException(status_code=401, detail="No user_id in token")

        loan = db.query(Loan).filter(Loan.id == loan_id).first()
        if loan is None:
            raise HTTPException(status_code=404, detail="Loan not found")
        if (
            data.get("role") not in ("admin", "librarian")
            and loan.user_id != user_id
        ):
            raise HTTPException(
                status_code=403,
                detail="Cannot return another user's loan",
            )

        loan = return_book(db, loan_id)
        return _loan_to_dict(loan)

    return router
