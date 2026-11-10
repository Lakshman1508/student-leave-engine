"""Database layer: connection helper and schema creation."""
import os
import sqlite3

# Override with the DB_PATH environment variable if you mount a persistent disk.
DB_NAME = os.getenv("DB_PATH", "system.db")


def get_connection() -> sqlite3.Connection:
    # check_same_thread=False: FastAPI runs sync endpoints and dependencies
    # in a thread pool, so one request may touch the connection from
    # more than one thread. Each request still gets its own connection.
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite ignores FKs by default
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                department TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS leave_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                leave_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students (student_id),
                UNIQUE (student_id, leave_date)
            );

            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                attendance_date TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN ('PRESENT', 'ABSENT', 'LATE', 'EXCUSED')),
                FOREIGN KEY (student_id) REFERENCES students (student_id),
                UNIQUE (student_id, attendance_date)
            );
        """)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully!")