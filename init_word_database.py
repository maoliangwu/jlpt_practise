from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "jlpt_practice.db"
WORDLIST = ROOT / "wordlist.txt"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify_group(name: str) -> str:
    return "group_" + uuid.uuid5(uuid.NAMESPACE_URL, name.strip()).hex[:16]


def parse_words(text: str) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        term = raw.strip().strip("\ufeff")
        if not term or term.startswith("#") or term.startswith("--"):
            continue
        if term not in seen:
            seen.add(term)
            words.append(term)
    return words


def column_names(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def add_column(con: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in column_names(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def init_schema(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS word_groups (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          marker TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL UNIQUE,
          source_filename TEXT,
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vocabulary (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          term TEXT NOT NULL UNIQUE,
          reading TEXT,
          meaning TEXT,
          part_of_speech TEXT,
          difficulty INTEGER NOT NULL DEFAULT 5 CHECK (difficulty BETWEEN 1 AND 5),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS word_group_items (
          group_id INTEGER NOT NULL REFERENCES word_groups(id) ON DELETE CASCADE,
          vocab_id INTEGER NOT NULL REFERENCES vocabulary(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL,
          PRIMARY KEY (group_id, vocab_id)
        );
        """
    )

    add_column(con, "word_groups", "description TEXT")
    add_column(con, "word_groups", "is_builtin INTEGER NOT NULL DEFAULT 0")
    add_column(con, "word_groups", "updated_at TEXT")
    add_column(con, "word_groups", "deleted_at TEXT")

    add_column(con, "vocabulary", "jlpt_level TEXT")
    add_column(con, "vocabulary", "moji_url TEXT")
    add_column(con, "vocabulary", "meaning_source TEXT")
    add_column(con, "vocabulary", "pos_source TEXT")
    add_column(con, "vocabulary", "is_manual INTEGER NOT NULL DEFAULT 0")
    add_column(con, "vocabulary", "initial_difficulty INTEGER NOT NULL DEFAULT 5")
    add_column(con, "vocabulary", "last_reviewed_at TEXT")
    add_column(con, "vocabulary", "next_review_at TEXT")
    add_column(con, "vocabulary", "review_count INTEGER NOT NULL DEFAULT 0")
    add_column(con, "vocabulary", "familiar_count INTEGER NOT NULL DEFAULT 0")
    add_column(con, "vocabulary", "unfamiliar_count INTEGER NOT NULL DEFAULT 0")
    add_column(con, "vocabulary", "deleted_at TEXT")

    add_column(con, "word_group_items", "display_order INTEGER NOT NULL DEFAULT 0")
    add_column(con, "word_group_items", "deleted_at TEXT")

    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS word_review_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          vocab_id INTEGER NOT NULL REFERENCES vocabulary(id) ON DELETE CASCADE,
          group_id INTEGER REFERENCES word_groups(id) ON DELETE SET NULL,
          old_difficulty INTEGER,
          new_difficulty INTEGER NOT NULL CHECK (new_difficulty BETWEEN 1 AND 5),
          result TEXT,
          note TEXT,
          reviewed_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_word_groups_marker ON word_groups(marker);
        CREATE INDEX IF NOT EXISTS idx_word_groups_deleted ON word_groups(deleted_at);
        CREATE INDEX IF NOT EXISTS idx_vocabulary_difficulty ON vocabulary(difficulty DESC);
        CREATE INDEX IF NOT EXISTS idx_vocabulary_term ON vocabulary(term);
        CREATE INDEX IF NOT EXISTS idx_vocabulary_recommend
          ON vocabulary(deleted_at, difficulty DESC, next_review_at, last_reviewed_at);
        CREATE INDEX IF NOT EXISTS idx_word_group_items_vocab ON word_group_items(vocab_id);
        CREATE INDEX IF NOT EXISTS idx_word_group_items_group_order
          ON word_group_items(group_id, deleted_at, display_order);
        CREATE INDEX IF NOT EXISTS idx_word_review_logs_vocab_time
          ON word_review_logs(vocab_id, reviewed_at DESC);
        """
    )


def ensure_core_words(con: sqlite3.Connection) -> dict[str, int]:
    marker = slugify_group("n3核心")
    existing = con.execute("SELECT id FROM word_groups WHERE marker = ?", (marker,)).fetchone()
    if existing:
        group_id = int(existing[0])
        con.execute(
            """
            UPDATE word_groups
            SET name = 'n3核心',
                source_filename = 'wordlist.txt',
                is_builtin = 1,
                updated_at = ?,
                deleted_at = NULL
            WHERE id = ?
            """,
            (now_iso(), group_id),
        )
    else:
        cur = con.execute(
            """
            INSERT INTO word_groups
              (marker, name, source_filename, description, is_builtin, created_at, updated_at)
            VALUES (?, 'n3核心', 'wordlist.txt', 'N3核心单词初始词组', 1, ?, ?)
            """,
            (marker, now_iso(), now_iso()),
        )
        group_id = int(cur.lastrowid)

    parsed = parse_words(WORDLIST.read_text(encoding="utf-8"))
    inserted_words = 0
    linked_words = 0
    for index, term in enumerate(parsed, start=1):
        moji_url = f"https://www.mojidict.com/searchText/{term}"
        cur = con.execute(
            """
            INSERT INTO vocabulary
              (term, jlpt_level, moji_url, difficulty, initial_difficulty, created_at, updated_at)
            VALUES (?, 'N3', ?, 5, 5, ?, ?)
            ON CONFLICT(term) DO UPDATE SET
              jlpt_level = COALESCE(vocabulary.jlpt_level, excluded.jlpt_level),
              moji_url = excluded.moji_url,
              updated_at = vocabulary.updated_at,
              deleted_at = NULL
            RETURNING id
            """,
            (term, moji_url, now_iso(), now_iso()),
        )
        vocab_id = int(cur.fetchone()[0])
        if con.total_changes:
            pass
        before = con.total_changes
        con.execute(
            """
            INSERT OR IGNORE INTO word_group_items
              (group_id, vocab_id, display_order, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (group_id, vocab_id, index, now_iso()),
        )
        if con.total_changes > before:
            linked_words += 1

    inserted_words = con.execute("SELECT COUNT(*) FROM vocabulary WHERE deleted_at IS NULL").fetchone()[0]
    group_words = con.execute(
        """
        SELECT COUNT(*)
        FROM word_group_items
        WHERE group_id = ? AND deleted_at IS NULL
        """,
        (group_id,),
    ).fetchone()[0]
    return {
        "group_id": group_id,
        "parsed": len(parsed),
        "active_vocabulary": int(inserted_words),
        "core_group_words": int(group_words),
        "new_links": linked_words,
    }


def main() -> None:
    with sqlite3.connect(DB_PATH) as con:
        init_schema(con)
        result = ensure_core_words(con)
        con.commit()
    print(f"initialized {DB_PATH}")
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
