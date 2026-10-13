"""搜索索引 + 采集源配置（SQLite，可后续替换为 ES）。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SearchHit:
    chat_id: int
    message_id: int
    chat_title: str
    text: str
    media_type: str
    created_at: int


@dataclass
class SourceChat:
    chat_id: int
    title: str
    username: str
    chat_type: str  # group / supergroup / channel
    enabled: bool


class SearchIndex:
    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                chat_title TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL DEFAULT '',
                media_type TEXT NOT NULL DEFAULT 'text',
                created_at INTEGER NOT NULL DEFAULT 0,
                UNIQUE(chat_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id);

            -- 采集源：群 / 频道
            CREATE TABLE IF NOT EXISTS sources (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                chat_type TEXT NOT NULL DEFAULT 'supergroup',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL DEFAULT 0
            );

            -- 允许发起搜索的群（用户交互入口）
            CREATE TABLE IF NOT EXISTS search_chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        self.conn.commit()

    # ---------- sources ----------
    def add_source(
        self,
        *,
        chat_id: int,
        title: str = "",
        username: str = "",
        chat_type: str = "supergroup",
        created_at: int = 0,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO sources (chat_id, title, username, chat_type, enabled, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
              title=excluded.title,
              username=excluded.username,
              chat_type=excluded.chat_type,
              enabled=1
            """,
            (chat_id, title or "", username or "", chat_type or "supergroup", created_at),
        )
        self.conn.commit()

    def remove_source(self, chat_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM sources WHERE chat_id=?", (chat_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def list_sources(self, enabled_only: bool = False) -> list[SourceChat]:
        sql = "SELECT chat_id, title, username, chat_type, enabled FROM sources"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY created_at DESC, chat_id DESC"
        rows = self.conn.execute(sql).fetchall()
        return [
            SourceChat(
                chat_id=r["chat_id"],
                title=r["title"],
                username=r["username"],
                chat_type=r["chat_type"],
                enabled=bool(r["enabled"]),
            )
            for r in rows
        ]

    def is_source(self, chat_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sources WHERE chat_id=? AND enabled=1",
            (chat_id,),
        ).fetchone()
        return row is not None

    # ---------- search entry chats ----------
    def add_search_chat(self, *, chat_id: int, title: str = "", username: str = "") -> None:
        self.conn.execute(
            """
            INSERT INTO search_chats (chat_id, title, username, enabled)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(chat_id) DO UPDATE SET
              title=excluded.title,
              username=excluded.username,
              enabled=1
            """,
            (chat_id, title or "", username or ""),
        )
        self.conn.commit()

    def remove_search_chat(self, chat_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM search_chats WHERE chat_id=?", (chat_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def list_search_chats(self) -> list[SourceChat]:
        rows = self.conn.execute(
            "SELECT chat_id, title, username, 'supergroup' AS chat_type, enabled FROM search_chats ORDER BY chat_id"
        ).fetchall()
        return [
            SourceChat(
                chat_id=r["chat_id"],
                title=r["title"],
                username=r["username"],
                chat_type=r["chat_type"],
                enabled=bool(r["enabled"]),
            )
            for r in rows
        ]

    def is_search_chat(self, chat_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM search_chats WHERE chat_id=? AND enabled=1",
            (chat_id,),
        ).fetchone()
        return row is not None

    # ---------- messages ----------
    def upsert_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        chat_title: str,
        text: str,
        media_type: str,
        created_at: int,
    ) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.conn.execute(
            """
            INSERT INTO messages (chat_id, message_id, chat_title, text, media_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
              chat_title=excluded.chat_title,
              text=excluded.text,
              media_type=excluded.media_type,
              created_at=excluded.created_at
            """,
            (chat_id, message_id, chat_title, text, media_type, created_at),
        )
        self.conn.commit()

    def search(self, query: str, *, offset: int = 0, limit: int = 8) -> tuple[list[SearchHit], int]:
        q = (query or "").strip()
        if not q:
            return [], 0
        tokens = [t for t in q.split() if t]
        if not tokens:
            return [], 0

        where = " AND ".join(["text LIKE ?"] * len(tokens))
        params = [f"%{t}%" for t in tokens]
        cur = self.conn.cursor()
        total = cur.execute(f"SELECT COUNT(*) AS c FROM messages WHERE {where}", params).fetchone()["c"]
        rows = cur.execute(
            f"""
            SELECT chat_id, message_id, chat_title, text, media_type, created_at
            FROM messages
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
        hits = [
            SearchHit(
                chat_id=r["chat_id"],
                message_id=r["message_id"],
                chat_title=r["chat_title"],
                text=r["text"],
                media_type=r["media_type"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
        return hits, int(total)

    def count_by_chat(self, chat_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE chat_id=?",
            (chat_id,),
        ).fetchone()
        return int(row["c"])

    def stats(self) -> dict:
        cur = self.conn.cursor()
        total = cur.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        chats = cur.execute("SELECT COUNT(DISTINCT chat_id) AS c FROM messages").fetchone()["c"]
        sources = cur.execute("SELECT COUNT(*) AS c FROM sources WHERE enabled=1").fetchone()["c"]
        search_chats = cur.execute("SELECT COUNT(*) AS c FROM search_chats WHERE enabled=1").fetchone()["c"]
        return {
            "messages": int(total),
            "indexed_chats": int(chats),
            "sources": int(sources),
            "search_chats": int(search_chats),
        }

    def close(self) -> None:
        self.conn.close()
