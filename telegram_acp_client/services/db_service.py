
import aiosqlite

from telegram_acp_client.config import settings


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

    async def get_last_session_id(self, chat_id: int) -> int | None:
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

    async def get_sessions(self, chat_id: int) -> list[tuple[int, str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, name, path FROM sessions WHERE chat_id = ? ORDER BY id DESC LIMIT 10",
                (chat_id,)
            ) as cursor:
                return await cursor.fetchall()

    async def get_session(self, sid: int) -> tuple[str, str] | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT name, path FROM sessions WHERE id = ?", (sid,)) as cursor:
                return await cursor.fetchone()

    async def get_session_by_name(self, chat_id: int, name: str) -> int | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT id FROM sessions WHERE chat_id = ? AND name = ?", (chat_id, name)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

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

    async def export_and_delete_session(self, session_id: int) -> str | None:
        async with aiosqlite.connect(self.db_path) as db:
            # 1. Get session info
            async with db.execute("SELECT name, path FROM sessions WHERE id = ?", (session_id,)) as cursor:
                session_info = await cursor.fetchone()
                if not session_info:
                    return None

            # 2. Get all messages for export
            async with db.execute(
                "SELECT role, content, timestamp FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,)
            ) as cursor:
                messages = await cursor.fetchall()

            # 3. Export to log file
            log_filename = f"session_{session_id}_{session_info[0]}_export.log"
            log_filepath = settings.DATA_DIR / log_filename
            with open(log_filepath, "w", encoding="utf-8") as f:
                f.write(f"Session ID: {session_id}\nName: {session_info[0]}\nPath: {session_info[1]}\n")
                f.write("=" * 40 + "\n\n")
                for role, content, timestamp in messages:
                    f.write(f"[{timestamp}] {role.upper()}:\n{content}\n")
                    f.write("-" * 40 + "\n")

            # 4. Delete messages
            await db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

            # 5. Delete session
            await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

            # 6. Unset last_session_id if it matches
            await db.execute(
                "UPDATE chat_state SET last_session_id = NULL WHERE last_session_id = ?",
                (session_id,)
            )

            await db.commit()
            return str(log_filepath)


db_service = DBService()
