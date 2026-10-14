"""Bounded SQLite cache and cross-process upstream coordination."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

_DB = Path(__file__).resolve().parents[2] / "data" / "cache.db"
MAX_ENTRIES = 2000
MAX_VALUE_BYTES = 4_000_000
MAX_CACHE_BYTES = 64_000_000


class Busy(Exception):
    """The bounded upstream queue or request capacity is full."""


def _conn():
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(_DB, timeout=2)
    try:
        c.execute(
            "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, expires REAL)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS leases (k TEXT, token TEXT PRIMARY KEY, expires REAL)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS pacing (k TEXT PRIMARY KEY, next_start REAL)"
        )
    except BaseException:
        c.close()
        raise
    return c


def get(key):
    with closing(_conn()) as c, c:
        row = c.execute("SELECT v, expires FROM kv WHERE k = ?", (key,)).fetchone()
        if not row:
            return None
        value, expires = row
        if expires is not None and expires <= time.time():
            c.execute("DELETE FROM kv WHERE k = ?", (key,))
            return None
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            c.execute("DELETE FROM kv WHERE k = ?", (key,))
            return None


def set(key, value, ttl=30 * 24 * 3600):
    encoded = json.dumps(value, allow_nan=False)
    if len(encoded.encode()) > MAX_VALUE_BYTES:
        return
    expires = time.time() + ttl
    with closing(_conn()) as c, c:
        c.execute(
            "DELETE FROM kv WHERE expires IS NULL OR expires <= ?", (time.time(),)
        )
        c.execute(
            "INSERT INTO kv VALUES (?, ?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, expires=excluded.expires",
            (key, encoded, expires),
        )
        c.execute(
            "DELETE FROM kv WHERE k IN (SELECT k FROM kv ORDER BY expires DESC LIMIT -1 OFFSET ?)",
            (MAX_ENTRIES,),
        )
        c.execute(
            "DELETE FROM kv WHERE k IN (SELECT k FROM (SELECT k, "
            "SUM(length(CAST(v AS BLOB))) OVER (ORDER BY expires DESC, k) AS size FROM kv) WHERE size > ?)",
            (MAX_CACHE_BYTES,),
        )


def key(namespace, value):
    return namespace + ":" + hashlib.sha256(value.encode()).hexdigest()


@contextmanager
def lease(name, *, limit=1, wait=0, ttl=120):
    """Bound concurrent work across local workers; recover after worker crashes."""
    token = uuid.uuid4().hex
    deadline = time.monotonic() + wait
    while True:
        with closing(_conn()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM leases WHERE expires <= ?", (time.time(),))
            count = c.execute(
                "SELECT count(*) FROM leases WHERE k=?", (name,)
            ).fetchone()[0]
            if count < limit:
                c.execute(
                    "INSERT INTO leases VALUES (?, ?, ?)",
                    (name, token, time.time() + ttl),
                )
                break
        if time.monotonic() >= deadline:
            raise Busy("Service busy; retry shortly")
        time.sleep(0.05)
    try:
        yield
    finally:
        with closing(_conn()) as c, c:
            c.execute("DELETE FROM leases WHERE token=?", (token,))


def pace(name, interval=1.1):
    """Reserve a request start time; call while holding the service lease."""
    with closing(_conn()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT next_start FROM pacing WHERE k=?", (name,)).fetchone()
        now = time.time()
        start = max(now, row[0] if row else now)
        c.execute(
            "INSERT INTO pacing VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET next_start=excluded.next_start",
            (name, start + interval),
        )
    time.sleep(max(0, start - time.time()))
