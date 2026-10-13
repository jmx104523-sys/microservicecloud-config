"""搜索索引：默认 SQLite LIKE（中文友好）；可后续替换为 Elasticsearch / Meilisearch。"""

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
            CREATE INDEX IF NOT EXISTS idx_messages_text ON messages(text);
            """
        )
        self.conn.commit()

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

        # 多关键词 AND：每个词都要命中
        tokens = [t for t in q.split() if t]
        if not tokens:
            return [], 0

        where = " AND ".join(["text LIKE ?"] * len(tokens))
        params = [f"%{t}%" for t in tokens]

        cur = self.conn.cursor()
        total = cur.execute(
            f"SELECT COUNT(*) AS c FROM messages WHERE {where}",
            params,
        ).fetchone()["c"]

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

    def stats(self) -> dict:
        cur = self.conn.cursor()
        total = cur.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        chats = cur.execute("SELECT COUNT(DISTINCT chat_id) AS c FROM messages").fetchone()["c"]
        return {"messages": int(total), "chats": int(chats)}

    def close(self) -> None:
        self.conn.close()
