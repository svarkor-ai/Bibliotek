"""Seed the Bibliotek demo database with a small sample catalog.

Usage (from repo root):
    .venv/bin/python scripts/seed.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException
from src.books import create_book  # noqa: E402
from src.database import get_session_cm  # noqa: E402

SAMPLE_BOOKS = [
    dict(isbn="9789175037187", title="Bröderna Lejonhjärta", author="Astrid Lindgren",
         publisher="Rabén & Sjögren", year=1973, hcf_category="hcf"),
    dict(isbn="9789100117481", title="Den allvarsamma leken", author="Hjalmar Söderberg",
         publisher="Albert Bonniers", year=1912, hcf_category="adult"),
    dict(isbn="9780141439518", title="Pride and Prejudice", author="Jane Austen",
         publisher="Penguin", year=1813, hcf_category="adult"),
]


def main() -> int:
    seeded = 0
    skipped = 0
    with get_session_cm() as db:
        for book in SAMPLE_BOOKS:
            try:
                create_book(db, **book)
                seeded += 1
            except HTTPException as exc:
                # duplicate ISBN on re-run is a no-op, not an error
                print(f"skip {book['isbn']}: {exc.detail}")
                skipped += 1
            except Exception as exc:
                print(f"skip {book['isbn']}: {type(exc).__name__}: {exc}")
                skipped += 1
    print(f"SEEDED {seeded} books ({skipped} skipped)")
    return 0  # idempotent: existing data is a success, not a failure


if __name__ == "__main__":
    raise SystemExit(main())
