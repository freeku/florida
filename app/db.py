import json
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "/data/poi.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
  shortcode TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  caption TEXT,
  status TEXT NOT NULL DEFAULT 'queued',   -- queued | parsed | needs_text | error
  error TEXT,
  parsed TEXT,                             -- JSON met kandidaten + resolve-resultaat
  created TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pois (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  place_id TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  address TEXT,
  lat REAL NOT NULL,
  lng REAL NOT NULL,
  category TEXT,
  gmaps_url TEXT,
  status TEXT NOT NULL DEFAULT 'new',      -- new | keep | reject
  synced INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  km_miami REAL,
  km_keylargo REAL,
  created TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS poi_sources (
  poi_id INTEGER NOT NULL REFERENCES pois(id) ON DELETE CASCADE,
  shortcode TEXT NOT NULL REFERENCES posts(shortcode) ON DELETE CASCADE,
  confidence REAL,
  evidence TEXT,
  PRIMARY KEY (poi_id, shortcode)
);
"""


@contextmanager
def conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(pois)")}
        for col, typ in (("wl_block_id", "INTEGER"), ("wl_list", "TEXT")):
            if col not in cols:
                c.execute(f"ALTER TABLE pois ADD COLUMN {col} {typ}")


def rows(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None


def run(sql, args=()):
    with conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def jdump(o):
    return json.dumps(o, ensure_ascii=False)
