"""First-boot bulk catalog import (MC 1932.2).

Streams a gzipped JSONL artifact (data/bulk_books.jsonl.gz, shipped inside
the repo) straight into the `books` table with raw stdlib sqlite3 — gzip,
json, sqlite3, pathlib only, so importing this module adds NO new dependency
to requirements.txt. Deliberately bypasses the SQLAlchemy ORM/Session for
this one path: it lets the import run before/without booting the rest of
the app's object graph, and batched raw executemany() keeps peak memory flat
regardless of catalog size — required because the app's systemd unit has
MemoryMax=512M and the catalog can be 100k+ rows.

Idempotent via a version-marker file next to the resolved db file: import
only runs when the marker is missing or holds a different version, so a
normal restart (marker already current) is a few filesystem stat calls, not
a 100k-row re-import.
"""

from __future__ import annotations

import gzip
import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Bump this whenever data/bulk_books.jsonl.gz is replaced/regenerated — that
# is what triggers a re-import on the next boot. Without a bump, an already-
# imported state dir's marker short-circuits and a refreshed artifact is
# silently never applied (the whole failure mode this marker exists to avoid).
# 2026-08-17.1 = initial 40,573-row snapshot (mid-scrape). 2026-08-17.2 = full
# completed scrape, all 39 OpenLibrary categories + Libris, 115,360 rows.
BULK_IMPORT_VERSION = "2026-08-17.2"
BULK_SOURCE_TAG = "bulk-2026-08-17"
BATCH_SIZE = 1000

_APP_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = _APP_ROOT / "data" / "bulk_books.jsonl.gz"

INSERT_COLS = (
    "isbn", "title", "author", "publisher", "year", "cover_url",
    "hcf_category", "dewey_number", "sab_signum", "subjects", "languages",
    "source", "created_at", "created_by",
)
INSERT_SQL = "INSERT INTO books ({cols}) VALUES ({ph})".format(
    cols=",".join(INSERT_COLS), ph=",".join("?" * len(INSERT_COLS))
)


def _marker_path(db_path: Path) -> Path:
    return db_path.parent / f".{db_path.name}.bulk_import_version"


def _already_current(marker: Path) -> bool:
    try:
        return marker.read_text().strip() == BULK_IMPORT_VERSION
    except FileNotFoundError:
        return False


def run_bulk_import_if_needed(artifact_path: Path | None = None) -> bool:
    """Import the bundled bulk catalog if present and not already applied.

    Returns True if an import artifact exists for this version (whether it
    was just imported, or already up to date from a previous boot) — the
    caller should treat this as "the catalog is/will be populated, skip any
    tiny demo seed". Returns False only when no artifact is bundled at all
    (bare/dev checkout without data/bulk_books.jsonl.gz) so the caller can
    fall back to the small demo seed.
    """
    artifact = artifact_path or ARTIFACT_PATH
    if not artifact.exists():
        logger.info("bulk_import: no artifact at %s, skipping", artifact)
        return False

    from src.database import resolve_db_path

    db_path = resolve_db_path()
    marker = _marker_path(db_path)

    if _already_current(marker):
        logger.info("bulk_import: db already at version %s, skipping", BULK_IMPORT_VERSION)
        return True

    logger.info(
        "bulk_import: importing %s into %s (version %s)",
        artifact, db_path, BULK_IMPORT_VERSION,
    )
    inserted = 0
    skipped = 0

    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        cur = con.cursor()

        existing_isbn = {
            r[0] for r in cur.execute(
                "SELECT isbn FROM books WHERE isbn IS NOT NULL AND isbn != ''"
            )
        }
        existing_no_isbn = {
            ((r[0] or "").strip().lower(), (r[1] or "").strip().lower())
            for r in cur.execute(
                "SELECT title, author FROM books WHERE isbn IS NULL OR isbn = ''"
            )
        }

        batch: list[tuple] = []

        def flush() -> None:
            nonlocal batch
            if batch:
                cur.executemany(INSERT_SQL, batch)
                con.commit()
                batch = []

        with gzip.open(artifact, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                b = json.loads(line)

                isbn = b.get("isbn") or None
                if isbn is not None:
                    if isbn in existing_isbn:
                        skipped += 1
                        continue
                    existing_isbn.add(isbn)
                else:
                    key = (
                        (b.get("title") or "").strip().lower(),
                        (b.get("author") or "").strip().lower(),
                    )
                    if key in existing_no_isbn:
                        skipped += 1
                        continue
                    existing_no_isbn.add(key)

                batch.append((
                    isbn,
                    b.get("title") or "",
                    b.get("author"),
                    b.get("publisher"),
                    b.get("year"),
                    b.get("cover_url"),
                    b.get("hcf_category"),
                    b.get("dewey_number"),
                    b.get("sab_signum"),
                    b.get("subjects"),
                    b.get("languages"),
                    b.get("source") or BULK_SOURCE_TAG,
                    b.get("created_at"),
                    None,  # created_by
                ))
                inserted += 1
                if len(batch) >= BATCH_SIZE:
                    flush()

        flush()
    finally:
        con.close()

    marker.write_text(BULK_IMPORT_VERSION)
    logger.info("bulk_import: done — inserted=%d skipped=%d", inserted, skipped)
    return True
