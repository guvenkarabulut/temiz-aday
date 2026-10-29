import os
import sqlite3

# shortcut: tek kiracı, SQLite. Çok kiracılı kullanıma geçerken PostgreSQL + RLS (README'de yazılı).
SCHEMA = """
CREATE TABLE IF NOT EXISTS position (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  dept TEXT NOT NULL DEFAULT '',
  jd_text TEXT NOT NULL,
  keywords TEXT NOT NULL DEFAULT '',
  thr REAL NOT NULL DEFAULT 0.25,
  margin REAL NOT NULL DEFAULT 0.08,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS application (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  position_id INTEGER NOT NULL REFERENCES position(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  cv_text TEXT NOT NULL DEFAULT '',
  tokens_est INTEGER NOT NULL DEFAULT 0,
  score REAL,
  must_ok INTEGER NOT NULL DEFAULT 1,
  matched TEXT NOT NULL DEFAULT '',
  decision TEXT NOT NULL,            -- rejected | review | llm | unreadable
  ocr INTEGER NOT NULL DEFAULT 0,
  decided_by TEXT NOT NULL DEFAULT 'filter',  -- filter | human
  note TEXT NOT NULL DEFAULT '',
  filter_ms REAL NOT NULL DEFAULT 0,
  llm_state TEXT NOT NULL DEFAULT 'none',     -- none | pending | done | failed | disabled
  llm_score INTEGER,
  llm_summary TEXT NOT NULL DEFAULT '',
  llm_tokens_in INTEGER NOT NULL DEFAULT 0,
  llm_tokens_out INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (position_id, sha256)
);
CREATE INDEX IF NOT EXISTS app_pos ON application (position_id, decision, created_at DESC);
"""


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    c = sqlite3.connect(path, timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init(path: str) -> None:
    c = connect(path)
    c.executescript(SCHEMA)
    if 'ocr' not in {r[1] for r in c.execute('PRAGMA table_info(application)')}:  # eski veritabanları
        c.execute('ALTER TABLE application ADD COLUMN ocr INTEGER NOT NULL DEFAULT 0')
    c.commit()
    c.close()
