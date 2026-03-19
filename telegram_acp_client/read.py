import os
import sqlite3
import sys


def read_session(session_id: int):
    db_path = "database.db"
    if not os.path.exists(db_path):
        print(f"Error: Database file '{db_path}' not found.")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get session info
        cursor.execute(
            "SELECT name, path, created_at FROM sessions WHERE id = ?", (session_id,)
        )
        session = cursor.fetchone()

        if not session:
            print(f"Error: Session ID {session_id} not found in database.")
            return

        name, path, created_at = session
        print("=" * 60)
        print(f"SESSION ID: {session_id}")
        print(f"NAME:       {name}")
        print(f"PATH:       {path}")
        print(f"CREATED AT: {created_at}")
        print("=" * 60)
        print("\n")

        # Get messages
        cursor.execute(
            "SELECT role, content, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,),
        )
        messages = cursor.fetchall()

        if not messages:
            print("No messages found for this session.")
        else:
            for role, content, timestamp in messages:
                role_label = f"[{role.upper()}]".ljust(10)
                print(f"{timestamp} {role_label}")
                print("-" * len(role_label))
                print(content)
                print("\n" + "." * 40 + "\n")

        conn.close()
    except sqlite3.Error as e:
        print(f"Database error: {e}")


def main():
    if len(sys.argv) < 2:
        print("Usage: telegram-acp-read <session_id>")
        sys.exit(1)

    try:
        sid = int(sys.argv[1])
        read_session(sid)
    except ValueError:
        print("Error: session_id must be an integer.")
        sys.exit(1)

if __name__ == "__main__":
    main()
