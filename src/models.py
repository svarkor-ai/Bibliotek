"""SQLAlchemy ORM models for Bibliotek."""

from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums (Python-level, mapped as VARCHAR in SQLite)
# ---------------------------------------------------------------------------

VALID_ROLES: tuple[str, ...] = ("admin", "librarian", "staff", "user")
VALID_HCF_CATEGORIES: tuple[str, ...] = ("hcf", "hcg", "hcb", "adult")


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )

    # Relationships
    books_created: Mapped[list["Book"]] = relationship(
        "Book", back_populates="creator", foreign_keys="Book.created_by"
    )
    loans_as_borrower: Mapped[list["Loan"]] = relationship(
        "Loan", back_populates="borrower", foreign_keys="Loan.user_id"
    )
    loans_as_librarian: Mapped[list["Loan"]] = relationship(
        "Loan", back_populates="librarian", foreign_keys="Loan.librarian_id"
    )


# ---------------------------------------------------------------------------
# Book
# ---------------------------------------------------------------------------

class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    isbn: Mapped[str | None] = mapped_column(String(500), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[str] = mapped_column(String(500), nullable=True)
    publisher: Mapped[str] = mapped_column(String(500), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    hcf_category: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    dewey_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sab_signum: Mapped[str | None] = mapped_column(String(10), nullable=True)
    subjects: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    languages: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    # Relationships
    creator: Mapped["User"] = relationship(
        "User", back_populates="books_created", foreign_keys=[created_by]
    )
    loans: Mapped[list["Loan"]] = relationship(
        "Loan", back_populates="book"
    )


# ---------------------------------------------------------------------------
# Loan
# ---------------------------------------------------------------------------

class Loan(Base):
    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    librarian_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    checkout_date: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    due_date: Mapped[datetime] = mapped_column(
        DateTime, nullable=False
    )
    return_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )

    # Relationships
    book: Mapped["Book"] = relationship("Book", back_populates="loans")
    borrower: Mapped["User"] = relationship(
        "User", back_populates="loans_as_borrower", foreign_keys=[user_id]
    )
    librarian: Mapped["User"] = relationship(
        "User", back_populates="loans_as_librarian", foreign_keys=[librarian_id]
    )


# ---------------------------------------------------------------------------
# HcfCategory
# ---------------------------------------------------------------------------

class HcfCategory(Base):
    __tablename__ = "hcf_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    min_age: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_age: Mapped[int] = mapped_column(Integer, nullable=False)

    def __repr__(self) -> str:
        return f"<HcfCategory(code={self.code!r}, name={self.name!r})>"
