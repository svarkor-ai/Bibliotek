# Bibliotek — Bibliotek med utlåning via streckkod

Webapplikation för bibliotek med:
- **Sökbar katalog** med ca 115 000 böcker (skördade från OpenLibrary + Libris),
  filtrerbara på HCF-kategori (Barn/Unga/Vuxen), språk, SAB-signum och DDC-nummer
- **Utlåning/inlämning** av böcker (28 dagar lånetid)
- **ISBN/EAN-streckkod** via mobilkamera
- **Användare, bibliotekarier och admin**
- **HCF-integration** för svensk bokidentifiering

> **Data:** Den live-driftsatta katalogen (~115k böcker) fylldes via en bulk-import
> utanför detta repo och kan inte återskapas från en ren klon — en lokal klon startar
> med en liten demokatalog. Kategorifiltret använder databasens HCF-koder
> (`hcf`/`hcg`/`hcb`/`adult`).

## Kör lokal

```bash
cd ~/svarkor/builds/bibliotek
uv venv .venv && . .venv/bin/activate
uv pip install -r requirements.txt
python src/app.py
```

Öppna `http://localhost:8140` i webbläsaren.

## Test

```bash
python -m pytest tests/ -v
```
