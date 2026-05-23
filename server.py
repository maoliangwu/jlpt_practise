from __future__ import annotations

import json
import mimetypes
import sqlite3
import uuid
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
APP_DB = ROOT / "jlpt_practice.db"
WORDLIST = ROOT / "wordlist.txt"
VERB_DB = ROOT / "verb_conjugation.db"
GRAMMAR_DB = ROOT / "grammar_lessons.db"
SPECIAL_DB = ROOT / "specialized_tranning.db"

POS_OPTIONS = [
    "名词",
    "动词",
    "い形容词",
    "な形容词",
    "副词",
    "连词",
    "助词",
    "表达",
    "寒暄",
    "其他",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_app() -> sqlite3.Connection:
    con = sqlite3.connect(APP_DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def connect_external(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def rows(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def row(con: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    item = con.execute(sql, params).fetchone()
    return dict(item) if item else None


def parse_word_lines(text: str) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        value = raw.strip().strip("\ufeff")
        if not value or value.startswith("#") or value.startswith("--"):
            continue
        if "\t" in value:
            value = value.split("\t", 1)[0].strip()
        if "," in value and len(value.split(",")) <= 4:
            value = value.split(",", 1)[0].strip()
        if value and value not in seen:
            seen.add(value)
            words.append(value)
    return words


def slugify_group(name: str) -> str:
    return "group_" + uuid.uuid5(uuid.NAMESPACE_URL, name.strip()).hex[:16]


def ensure_group(con: sqlite3.Connection, name: str, source_filename: str | None = None) -> int:
    marker = slugify_group(name)
    existing = row(con, "SELECT id FROM word_groups WHERE marker = ?", (marker,))
    if existing:
        return int(existing["id"])
    cur = con.execute(
        """
        INSERT INTO word_groups (marker, name, source_filename, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (marker, name, source_filename, now_iso()),
    )
    return int(cur.lastrowid)


def ensure_vocab(
    con: sqlite3.Connection,
    term: str,
    reading: str | None = None,
    meaning: str | None = None,
    part_of_speech: str | None = None,
) -> int:
    existing = row(con, "SELECT id, reading, meaning, part_of_speech FROM vocabulary WHERE term = ?", (term,))
    if existing:
        updates: dict[str, str] = {}
        if reading and not existing.get("reading"):
            updates["reading"] = reading
        if meaning and not existing.get("meaning"):
            updates["meaning"] = meaning
        if part_of_speech and not existing.get("part_of_speech"):
            updates["part_of_speech"] = part_of_speech
        if updates:
            columns = ", ".join(f"{key} = ?" for key in updates)
            con.execute(
                f"UPDATE vocabulary SET {columns}, updated_at = ? WHERE id = ?",
                (*updates.values(), now_iso(), existing["id"]),
            )
        return int(existing["id"])
    cur = con.execute(
        """
        INSERT INTO vocabulary
          (term, reading, meaning, part_of_speech, difficulty, created_at, updated_at)
        VALUES (?, ?, ?, ?, 5, ?, ?)
        """,
        (term, reading, meaning, part_of_speech, now_iso(), now_iso()),
    )
    return int(cur.lastrowid)


def add_vocab_to_group(con: sqlite3.Connection, group_id: int, vocab_id: int) -> None:
    con.execute(
        """
        INSERT OR IGNORE INTO word_group_items (group_id, vocab_id, created_at)
        VALUES (?, ?, ?)
        """,
        (group_id, vocab_id, now_iso()),
    )


def import_word_text(
    con: sqlite3.Connection,
    group_name: str,
    text: str,
    source_filename: str | None = None,
) -> dict:
    group_id = ensure_group(con, group_name, source_filename)
    words = parse_word_lines(text)
    before = con.total_changes
    for term in words:
        vocab_id = ensure_vocab(con, term)
        add_vocab_to_group(con, group_id, vocab_id)
    con.commit()
    return {
        "group_id": group_id,
        "group_name": group_name,
        "parsed": len(words),
        "changes": con.total_changes - before,
    }


def init_schema() -> None:
    with connect_app() as con:
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

            CREATE INDEX IF NOT EXISTS idx_vocabulary_difficulty ON vocabulary(difficulty DESC);
            CREATE INDEX IF NOT EXISTS idx_vocabulary_term ON vocabulary(term);
            CREATE INDEX IF NOT EXISTS idx_group_items_vocab ON word_group_items(vocab_id);
            """
        )
        if WORDLIST.exists():
            import_word_text(con, "n3核心", WORDLIST.read_text(encoding="utf-8"), WORDLIST.name)
        if SPECIAL_DB.exists():
            group_id = ensure_group(con, "专项练习词汇", SPECIAL_DB.name)
            special_con = connect_external(SPECIAL_DB)
            try:
                for item in special_con.execute(
                    """
                    SELECT expression, reading, meaning, category
                    FROM special_practice
                    ORDER BY id
                    """
                ):
                    vocab_id = ensure_vocab(
                        con,
                        item["expression"],
                        item["reading"],
                        item["meaning"],
                        "表达" if item["category"] == "寒暄问候" else None,
                    )
                    add_vocab_to_group(con, group_id, vocab_id)
            finally:
                special_con.close()
            con.commit()


def json_response(handler: BaseHTTPRequestHandler, data: object, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def error_response(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
    json_response(handler, {"error": message}, status)


def read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if not length:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def read_multipart(handler: BaseHTTPRequestHandler) -> dict:
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("Expected multipart/form-data")
    length = int(handler.headers.get("Content-Length", "0"))
    body = handler.rfile.read(length)
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    message = BytesParser(policy=default).parsebytes(header + body)
    fields: dict[str, object] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            fields[name] = {
                "filename": filename,
                "content": payload,
            }
        else:
            fields[name] = payload.decode(part.get_content_charset() or "utf-8").strip()
    return fields


class Handler(BaseHTTPRequestHandler):
    server_version = "JLPTPractice/1.0"

    def do_GET(self) -> None:
        try:
            self.route_get()
        except Exception as exc:  # noqa: BLE001
            error_response(self, 500, str(exc))

    def do_POST(self) -> None:
        try:
            self.route_post()
        except ValueError as exc:
            error_response(self, 400, str(exc))
        except Exception as exc:  # noqa: BLE001
            error_response(self, 500, str(exc))

    def do_PATCH(self) -> None:
        try:
            self.route_patch()
        except ValueError as exc:
            error_response(self, 400, str(exc))
        except Exception as exc:  # noqa: BLE001
            error_response(self, 500, str(exc))

    def do_DELETE(self) -> None:
        try:
            self.route_delete()
        except Exception as exc:  # noqa: BLE001
            error_response(self, 500, str(exc))

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def route_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/":
            self.send_static(STATIC / "index.html")
            return
        if path.startswith("/static/"):
            self.send_static(STATIC / unquote(path.removeprefix("/static/")))
            return

        if path == "/api/health":
            json_response(self, {"ok": True})
            return
        if path == "/api/pos-options":
            json_response(self, {"options": POS_OPTIONS})
            return
        if path == "/api/groups":
            self.get_groups()
            return
        if path == "/api/words":
            self.get_words(qs)
            return
        if path == "/api/grammar/lessons":
            self.get_grammar_lessons()
            return
        if path == "/api/verbs":
            self.get_verbs(qs)
            return
        if path == "/api/special/categories":
            self.get_special_categories()
            return
        if path == "/api/special":
            self.get_special(qs)
            return
        error_response(self, 404, "Not found")

    def route_post(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/groups/import":
            fields = read_multipart(self)
            upload = fields.get("file")
            if not isinstance(upload, dict):
                raise ValueError("缺少 txt 文件")
            filename = str(upload.get("filename") or "words.txt")
            group_name = str(fields.get("group_name") or Path(filename).stem).strip()
            if not group_name:
                raise ValueError("缺少词组名称")
            text = bytes(upload["content"]).decode("utf-8-sig")
            with connect_app() as con:
                result = import_word_text(con, group_name, text, filename)
            json_response(self, result)
            return
        error_response(self, 404, "Not found")

    def route_patch(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/words/"):
            vocab_id = int(parsed.path.rsplit("/", 1)[1])
            payload = read_json(self)
            allowed = {"reading", "meaning", "part_of_speech", "difficulty"}
            updates = {key: payload[key] for key in allowed if key in payload}
            if "difficulty" in updates:
                updates["difficulty"] = int(updates["difficulty"])
                if not 1 <= updates["difficulty"] <= 5:
                    raise ValueError("难度必须在 1 到 5 之间")
            if not updates:
                raise ValueError("没有可更新字段")
            with connect_app() as con:
                columns = ", ".join(f"{key} = ?" for key in updates)
                con.execute(
                    f"UPDATE vocabulary SET {columns}, updated_at = ? WHERE id = ?",
                    (*updates.values(), now_iso(), vocab_id),
                )
                con.commit()
                item = row(con, "SELECT * FROM vocabulary WHERE id = ?", (vocab_id,))
            json_response(self, item or {})
            return
        error_response(self, 404, "Not found")

    def route_delete(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/groups/"):
            group_id = int(path.rsplit("/", 1)[1])
            with connect_app() as con:
                con.execute("DELETE FROM word_groups WHERE id = ?", (group_id,))
                con.commit()
            json_response(self, {"ok": True})
            return
        if path.startswith("/api/group-items/"):
            parts = path.strip("/").split("/")
            if len(parts) != 4:
                error_response(self, 400, "Invalid group item path")
                return
            group_id, vocab_id = parts[2], parts[3]
            with connect_app() as con:
                con.execute(
                    "DELETE FROM word_group_items WHERE group_id = ? AND vocab_id = ?",
                    (int(group_id), int(vocab_id)),
                )
                con.commit()
            json_response(self, {"ok": True})
            return
        error_response(self, 404, "Not found")

    def send_static(self, path: Path) -> None:
        resolved = path.resolve()
        if not str(resolved).startswith(str(STATIC.resolve())) or not resolved.exists() or resolved.is_dir():
            error_response(self, 404, "Not found")
            return
        body = resolved.read_bytes()
        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def get_groups(self) -> None:
        with connect_app() as con:
            data = rows(
                con,
                """
                SELECT g.id, g.marker, g.name, g.source_filename, g.created_at,
                       COUNT(i.vocab_id) AS word_count
                FROM word_groups g
                LEFT JOIN word_group_items i ON i.group_id = g.id
                GROUP BY g.id
                ORDER BY g.created_at, g.id
                """,
            )
        json_response(self, {"groups": data})

    def get_words(self, qs: dict[str, list[str]]) -> None:
        group_id = qs.get("group_id", [""])[0]
        search = qs.get("search", [""])[0].strip()
        params: list[object] = []
        where = []
        join = ""
        if group_id:
            join = "JOIN word_group_items i ON i.vocab_id = v.id"
            where.append("i.group_id = ?")
            params.append(int(group_id))
        if search:
            where.append("(v.term LIKE ? OR v.meaning LIKE ? OR v.reading LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
            SELECT v.*
            FROM vocabulary v
            {join}
            {where_sql}
            ORDER BY v.difficulty DESC, v.updated_at ASC, v.id ASC
            LIMIT 500
        """
        with connect_app() as con:
            data = rows(con, sql, tuple(params))
        json_response(self, {"words": data})

    def get_grammar_lessons(self) -> None:
        with connect_external(GRAMMAR_DB) as con:
            data = rows(con, "SELECT * FROM grammar_lessons ORDER BY sort_order, id")
        json_response(self, {"lessons": data})

    def get_verbs(self, qs: dict[str, list[str]]) -> None:
        limit = min(int(qs.get("limit", ["80"])[0]), 300)
        with connect_external(VERB_DB) as con:
            data = rows(
                con,
                """
                SELECT *
                FROM verb_conjugation
                ORDER BY lesson, id
                LIMIT ?
                """,
                (limit,),
            )
        json_response(self, {"verbs": data})

    def get_special_categories(self) -> None:
        with connect_external(SPECIAL_DB) as con:
            data = rows(
                con,
                """
                SELECT category, COUNT(*) AS item_count
                FROM special_practice
                GROUP BY category
                ORDER BY MIN(id)
                """,
            )
        json_response(self, {"categories": data})

    def get_special(self, qs: dict[str, list[str]]) -> None:
        category = qs.get("category", [""])[0]
        params: list[object] = []
        where = ""
        if category:
            where = "WHERE s.category = ?"
            params.append(category)
        with connect_external(SPECIAL_DB) as special_con, connect_app() as app_con:
            data = rows(
                special_con,
                f"""
                SELECT s.*
                FROM special_practice s
                {where}
                ORDER BY s.id
                """,
                tuple(params),
            )
            for item in data:
                vocab = row(app_con, "SELECT * FROM vocabulary WHERE term = ?", (item["expression"],))
                item["vocab"] = vocab
                item["moji_url"] = f"https://www.mojidict.com/searchText/{item['expression']}"
            data.sort(key=lambda item: (-(item["vocab"] or {}).get("difficulty", 5), item["id"]))
        json_response(self, {"items": data})


def main() -> None:
    init_schema()
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("Serving JLPT Practice at http://127.0.0.1:8000")
    server.serve_forever()


if __name__ == "__main__":
    main()
