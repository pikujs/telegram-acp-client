import aiosqlite
import logging

from telegram_acp_client.config import settings

logger = logging.getLogger(__name__)


class DBService:
    def __init__(self, db_path: str = settings.DATABASE_PATH):
        self.db_path = db_path

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    thread_id INTEGER DEFAULT 0,
                    name TEXT,
                    path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            async with db.execute("PRAGMA table_info(sessions)") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]
                if "thread_id" not in columns:
                    await db.execute(
                        "ALTER TABLE sessions ADD COLUMN thread_id INTEGER DEFAULT 0"
                    )
                if "last_active_at" not in columns:
                    await db.execute(
                        "ALTER TABLE sessions ADD COLUMN last_active_at TIMESTAMP DEFAULT '1970-01-01 00:00:00'"
                    )

            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER,
                    role TEXT,
                    content TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions (id)
                )
            """)

            # Add explicit indexes for performance
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_chat_thread ON sessions(chat_id, thread_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON sessions(last_active_at)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id)"
            )

            await db.commit()

    async def set_last_session(
        self, chat_id: int, thread_id: int | None, session_id: int
    ):
        tid = thread_id or 0
        async with aiosqlite.connect(self.db_path) as db:
            if tid != 0:
                # For threads, we want to ensure only one session is "linked" to this thread.
                # Unlink any other session that was linked to this thread.
                await db.execute(
                    "UPDATE sessions SET thread_id = 0 WHERE chat_id = ? AND thread_id = ?",
                    (chat_id, tid),
                )
                # Link the new session to this thread.
                await db.execute(
                    "UPDATE sessions SET thread_id = ? WHERE id = ?",
                    (tid, session_id),
                )
            
            # Always update last_active_at to mark this as the most recently used session
            # in its respective context (main chat or thread).
            await db.execute(
                "UPDATE sessions SET last_active_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,),
            )
            await db.commit()

    async def get_last_session_id(
        self, chat_id: int, thread_id: int | None
    ) -> int | None:
        tid = thread_id or 0
        async with aiosqlite.connect(self.db_path) as db:
            # We sort by last_active_at DESC to get the session the user was actually on.
            async with db.execute(
                "SELECT id FROM sessions WHERE chat_id = ? AND thread_id = ? ORDER BY last_active_at DESC, id DESC LIMIT 1",
                (chat_id, tid),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def create_session(
        self, chat_id: int, thread_id: int | None, name: str, path: str
    ) -> int:
        tid = thread_id or 0
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO sessions (chat_id, thread_id, name, path, last_active_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (chat_id, tid, name, path),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_sessions(
        self, chat_id: int, thread_id: int | None
    ) -> list[tuple[int, str, str]]:
        tid = thread_id or 0
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, name, path FROM sessions WHERE chat_id = ? AND thread_id = ? ORDER BY id DESC LIMIT 10",
                (chat_id, tid),
            ) as cursor:
                return await cursor.fetchall()

    async def get_all_sessions(self, chat_id: int) -> list[tuple[int, str, str, int]]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, name, path, thread_id FROM sessions WHERE chat_id = ? ORDER BY id DESC LIMIT 20",
                (chat_id,),
            ) as cursor:
                return await cursor.fetchall()

    async def get_linked_sessions(self, chat_id: int) -> list[tuple[int, str, int]]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, name, thread_id FROM sessions WHERE chat_id = ? AND thread_id != 0 ORDER BY id DESC",
                (chat_id,),
            ) as cursor:
                return await cursor.fetchall()

    async def detach_session_from_thread(self, session_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE sessions SET thread_id = 0 WHERE id = ?",
                (session_id,),
            )
            await db.commit()

    async def get_session(self, sid: int) -> tuple[str, str] | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT name, path FROM sessions WHERE id = ?", (sid,)
            ) as cursor:
                return await cursor.fetchone()

    async def get_session_by_name(
        self, chat_id: int, thread_id: int | None, name: str
    ) -> int | None:
        tid = thread_id or 0
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id FROM sessions WHERE chat_id = ? AND thread_id = ? AND name = ?",
                (chat_id, tid, name),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def get_sessions_for_thread(self, chat_id: int) -> list[tuple[int, str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT s.id, s.name, s.path 
                FROM sessions s
                WHERE s.chat_id = ? 
                AND s.thread_id = 0
                ORDER BY s.id DESC
                """,
                (chat_id,),
            ) as cursor:
                return await cursor.fetchall()

    async def save_message(self, session_id, role, content):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )
            await db.execute(
                "UPDATE sessions SET last_active_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,),
            )
            await db.commit()

    async def get_recent_messages(self, session_id, limit=20):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return rows[::-1]

    async def export_and_delete_session(self, session_id: int) -> str | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT name, path FROM sessions WHERE id = ?", (session_id,)
            ) as cursor:
                session_info = await cursor.fetchone()
                if not session_info:
                    return None

            async with db.execute(
                "SELECT role, content, timestamp FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ) as cursor:
                messages = await cursor.fetchall()

            log_filename = f"session_{session_id}_{session_info[0]}_export.log"
            log_filepath = settings.DATA_DIR / log_filename
            with open(log_filepath, "w", encoding="utf-8") as f:
                f.write(
                    f"Session ID: {session_id}\nName: {session_info[0]}\nPath: {session_info[1]}\n"
                )
                f.write("=" * 40 + "\n\n")
                for role, content, timestamp in messages:
                    f.write(f"[{timestamp}] {role.upper()}:\n{content}\n")
                    f.write("-" * 40 + "\n")

            await db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

            await db.commit()
            return str(log_filepath)


db_service = DBService()
