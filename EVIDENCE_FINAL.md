# Application ## VERIFY_EXIT=0

## 1. Test suite baseline
Ran `.venv/bin/python -m pytest tests/ -q` -- PASS 102/102 (2d, following the 404-fix).
FIRST run: pytest missing from venv -> installed pytest + pytest-asyncio, then 102/102.

## 2. Live server on :8140 (background uvicorn, workdir=/srv/workspace/bibliotek)
- `GET /` 200
- `GET /catalog` 200 AND contains a seeded book title
- `GET /books/3` 200, contains book
- deps verified: `src/config.py` DATABASE_URL default `sqlite:///./bibliotek.db` resolved to cwd

## 3. Bug found+fixed: `GET /books/{id}` missing book -> 500 (jinja UndefinedError 'None' has no attribute 'title')
Fix in src/app.py book_detail_page: guard `if not book: return HTML 404`.
RETEST: 404, friendly text "hittades inte" (BEHAVIORAL + assert against HTTP code)

## 4. Catalog seeded via API (3 books) -- reproducible with scripts/seed.py
`/api/books` total=3 (Bröderna Lejonhjärta, Den allvarsamma leken, Pride and Prejudice)
scripts/seed.py idempotent: reruns skip existing ISBNs, exit 0 both times.
Direct SQL proved seeding must go through the server's own connection (saw 0 vs API 3 -- WAL). API path is authoritative.

## 5. Circulation lifecycle (live):
- register borrower -> id
- POST /api/loans/checkout {book_id,user_id,librarian_id} + admin Bearer token -> 200, due_date = checkout + 28 days (2026-09-13, checkout 08-16) -> 28-day policy VERIFIED
- POST /api/loans/return {loan_id} + token -> 200, return_date set, overdue:false

## 6. Git pushed
remote origin (SSH git@github.com:svarkor-ai/Bibliotek.git) push main: 70d803c..c370894
Commits: 6c7de48 fix(books) 404; c370894 seed script.

## VERIFY_EXIT=0
