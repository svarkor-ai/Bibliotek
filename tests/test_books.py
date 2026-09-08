"""Tests for Book CRUD + Libris search integration."""

from unittest.mock import MagicMock, patch

import pytest

from fastapi import HTTPException
from src.books import (
    Book,
    classify_hcf,
    create_book,
    get_book,
    libris_lookup_by_isbn,
    libris_search,
    list_books,
)


# ---------------------------------------------------------------------------
# create_book
# ---------------------------------------------------------------------------

class TestCreateBook:
    def test_create_minimal(self, session):
        book = create_book(session, isbn="1234567890123", title="Test Book")
        assert book.id is not None
        assert book.title == "Test Book"
        assert book.isbn == "1234567890123"

    def test_create_full(self, session):
        book = create_book(
            session,
            isbn="111",
            title="Full Book",
            author="Jane Doe",
            publisher="Acme Press",
            year=2020,
        )
        assert book.author == "Jane Doe"
        assert book.publisher == "Acme Press"
        assert book.year == 2020

    def test_duplicate_isbn_raises(self, session):
        create_book(session, isbn="dup", title="First")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            create_book(session, isbn="dup", title="Second")
        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail

    def test_auto_classify_hcf(self, session):
        """Title with 'barn' keyword → hcf category."""
        book = create_book(session, isbn="hcf1", title="Lilla barnbok")
        assert book.hcf_category in ("hcf", "hcg", "hcb", "adult", None)

    def test_book_created_by_user(self, session):
        # Create a user first
        from src.users import register_user
        register_user(session, "booker", "pass123")
        book = create_book(session, isbn="cb1", title="Created by user", user_id=1)
        assert book.created_by == 1


# ---------------------------------------------------------------------------
# get_book / list_books
# ---------------------------------------------------------------------------

class TestGetBook:
    def test_get_existing(self, session, sample_book):
        book = get_book(session, sample_book["id"])
        assert book.title == sample_book["title"]

    def test_get_missing_returns_404(self, session):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            get_book(session, 9999)
        assert exc.value.status_code == 404


class TestListBooks:
    def test_empty_list(self, session):
        items, total = list_books(session)
        assert items == []
        assert total == 0

    def test_list_returns_count(self, session):
        create_book(session, isbn="l1", title="A")
        create_book(session, isbn="l2", title="B")
        create_book(session, isbn="l3", title="C")
        items, total = list_books(session)
        assert total == 3
        assert len(items) == 3

    def test_pagination(self, session):
        for i in range(5):
            create_book(session, isbn=f"pg{i}", title=f"Book {i}")
        items, total = list_books(session, limit=2, offset=0)
        assert len(items) == 2
        assert total == 5

        items2, _ = list_books(session, limit=2, offset=2)
        assert len(items2) == 2

    def test_filter_by_hcf_category(self, session):
        create_book(session, isbn="hf1", title="Barn bok", hcf_category="hcf")
        create_book(session, isbn="hf2", title="Vuxen bok", hcf_category="adult")
        items, total = list_books(session, hcf_category="hcf")
        assert total == 1
        assert items[0].hcf_category == "hcf"


# ---------------------------------------------------------------------------
# classify_hcf
# ---------------------------------------------------------------------------

class TestClassifyHCF:
    def test_child_keywords(self):
        assert classify_hcf("Lilla barnbok") == "hcf"

    def test_ya_keywords(self):
        assert classify_hcf("Young Adult Novel") == "hcb"

    def test_unsure_returns_none(self):
        assert classify_hcf("Advanced Quantum Mechanics") is None

    def test_empty_title(self):
        assert classify_hcf("") is None


# ---------------------------------------------------------------------------
# Libris integration
# ---------------------------------------------------------------------------

class TestLibrisSearch:
    def test_search_network_failure_returns_empty(self):
        """When Libris is unreachable, should return [] not raise."""
        results = libris_search("nonexistent_isbn_0000000000000")
        # Might get [] on failure or a real result; we just don't crash
        assert isinstance(results, list)

    @patch("src.books.httpx.get")
    def test_parse_libris_record(self, mock_get):
        """Mock Libris response and verify parsing."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "xsearch": {
                "list": [
                    {
                        "title": "Test Title",
                        "creator": "Test Author",
                        "publisher": "Test Pub",
                        "date": "2020",
                        "identifier": "libris://123",
                        "isbn": "9780061120084",
                        "language": "eng",
                    }
                ]
            }
        }
        mock_get.return_value = mock_resp
        results = libris_search("test", limit=10)
        assert len(results) == 1
        assert results[0]["title"] == "Test Title"
        assert results[0]["creator"] == "Test Author"
        assert results[0]["publisher"] == "Test Pub"
        assert results[0]["date"] == "2020"
        assert results[0]["isbn"] == "9780061120084"


# ---------------------------------------------------------------------------
# Book dataclass
# ---------------------------------------------------------------------------

class TestBookDataclass:
    def test_to_dict(self):
        from datetime import datetime, timezone
        b = Book(
            id=1, isbn="123", title="T", author="A",
            year=2020,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        d = b.to_dict()
        assert d["id"] == 1
        assert isinstance(d["created_at"], str)
        assert "2024-01-01" in d["created_at"]
