#!/usr/bin/env python3
"""Inspect only ciphertext prefixes, never serialize Secret payloads."""
import sqlite3
from contextlib import closing
from pathlib import Path

path = Path('/var/lib/rancher/k3s/server/db/state.db')
if not path.exists():
    print('SQLite verification skipped: datastore is not embedded SQLite')
    raise SystemExit(0)
with closing(sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True)) as connection:
    rows = connection.execute('''
        SELECT k.value FROM kine k
        JOIN (SELECT name, MAX(id) id FROM kine WHERE name LIKE ? GROUP BY name) latest
        ON k.id = latest.id WHERE k.deleted = 0
        ''', ('/registry/secrets/%',)).fetchall()
    encrypted = sum(bytes(value or b'').startswith(b'k8s:enc:') for (value,) in rows)
    history = connection.execute('SELECT value, old_value FROM kine WHERE name LIKE ?', ('/registry/secrets/%',)).fetchall()
    plaintext = sum(bool(value) and not bytes(value).startswith(b'k8s:enc:') for row in history for value in row)
print(f'Current Secret records: {len(rows)}; encrypted: {encrypted}')
print(f'Historical plaintext Secret values awaiting Kine compaction: {plaintext}')
if not rows or encrypted != len(rows):
    raise SystemExit('Current Secrets are not all encrypted')
