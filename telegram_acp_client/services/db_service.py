import aiosqlite
from telegram_acp_client.config import settings
from typing import List, Tuple, Optional

class DBService:
    def __init__(self, db_path: str = settings.DATABASE_PATH):
        self.db_path = db_path

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    name TEXT,
                    path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await db.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER,
                    role TEXT,
                    content TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions (id)
                )
            ''')
            await db.execute('''
                CREATE TABLE IF NOT EXISTS chat_state (
                    chat_id INTEGER PRIMARY KEY,
                    last_session_id INTEGER,
                    FOREIGN KEY (last_session_id) REFERENCES sessions (id)
                )
            ''')
            await db.commit()

    async def set_last_session(self, chat_id: int, session_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO chat_state (chat_id, last_session_id) VALUES (?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET last_session_id = excluded.last_session_id",
                (chat_id, session_id)
            )
            await db.commit()

    async def get_last_session_id(self, chat_id: int) -> Optional[int]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT last_session_id FROM chat_state WHERE chat_id = ?", (chat_id,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def create_session(self, chat_id: int, name: str, path: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO sessions (chat_id, name, path) VALUES (?, ?, ?)",
                (chat_id, name, path)
            )
            await db.commit()
            return cursor.lastrowid

    async def get_sessions(self, chat_id: int) -> List[Tuple[int, str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, name, path FROM sessions WHERE chat_id = ? ORDER BY id DESC LIMIT 10", 
                (chat_id,)
            ) as cursor:
                return await cursor.fetchall()

    async def get_session(self, sid: int) -> Optional[Tuple[str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT name, path FROM sessions WHERE id = ?", (sid,)) as cursor:
                return await cursor.fetchone()

    async def save_message(self, session_id, role, content):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )
            await db.commit()

    async def get_recent_messages(self, session_id, limit=20):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                # Return in chronological order
                return rows[::-1]


db_service = DBService()
