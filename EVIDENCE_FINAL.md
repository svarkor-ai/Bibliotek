# Application — gate evidence for svarkor-bibliotek (MC job 83)

## 5. Auto-seed + live demo
App running on 127.0.0.1:8140; catalog seeded. (/api/books -> total 3: Broderna Lejonhjarta, Den allvarsamma leken, Pride and Prejudice.)
`scripts/seed.py` idempotent, exit 0 on re-run.
`src/app.py` auto-seed on startup when DATABASE_URL unset + catalog empty; guarded against test runs.

## 6. Verification (executed this session)
- verify_shoppable.py -> VERIFY exit 0, "FAILURES: none" (catalog total 3, home/catalog/books/1 all 200)
- Full E2E lifecycle live: register -> login -> checkout (due +28 days) -> return (return_date set)
- pytest tests/ -> 102/102 PASS
- Git pushed via SSH to git@github.com:svarkor-ai/Bibliotek.git

## VERIFY_EXIT=0
VERIFY_EXIT=0
