# Bibliotek — Bibliotek med utlåning via streckkod

Webapplikation för bibliotek med:
- **Sökbar katalog** med ca 115 000 böcker (skördade från OpenLibrary + Libris),
  filtrerbara på HCF-kategori (Barn/Unga/Vuxen), språk, SAB-signum och DDC-nummer
- **Utlåning/inlämning** av böcker (28 dagar lånetid)
- **ISBN/EAN-streckkod** via mobilkamera
- **Användare, bibliotekarier och admin**
- **HCF-integration** för svensk bokidentifiering

> **Data:** Hela katalogen (~115 000 böcker) kan återskapas från en ren klon:
> `data/bulk_books.jsonl.gz` ingår i repot och bulk-importeras automatiskt
> (115 360 rader) vid första start mot en tom databas. Kategorifiltret använder
> databasens HCF-koder (`hcf`/`hcg`/`hcb`/`adult`).

## Kör lokal

`SECRET_KEY` och `JWT_EXPIRE_HOURS` är hårdkrävade miljövariabler (ingen
default — starta utan dem och importen av `src.config` misslyckas). Den riktiga
startpunkten är `server.py`, inte `src/app.py` (som bara definierar appen):

```bash
cd ~/svarkor/builds/bibliotek
uv venv .venv && . .venv/bin/activate
uv pip install -r requirements.txt
SECRET_KEY="byt-till-en-riktig-hemlighet-16+" JWT_EXPIRE_HOURS=24 \
    python server.py
```

Öppna `http://localhost:8140` i webbläsaren.

## Test

```bash
SECRET_KEY="test-secret" JWT_EXPIRE_HOURS=24 ENABLE_DEMO_WRITE_GUARD=false \
    python -m pytest tests/ -v
```
