#!/usr/bin/env python3
"""Companion verify for EVIDENCE_FINAL.md — proves the live demo is up and seeded."""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8140"


def status(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


failures = []

# 1. Catalog returns the 3 seeded books
s, b = status("/api/books")
j = json.loads(b)
if s != 200 or j.get("total") != 3:
    failures.append(f"/api/books total={j.get('total')}")
titles = {it.get("title") for it in j.get("items", [])}
if "Pride and Prejudice" not in titles:
    failures.append("missing Pride and Prejudice")

# 2. Home and catalog pages serve
for p in ("/", "/catalog", "/books/1"):
    s, b = status(p)
    if s != 200:
        failures.append(f"{p} -> {s}")

print("FAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
