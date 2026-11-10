"""Student Attendance & Leave Management Engine (FastAPI + SQLite)."""
import os
import secrets
import sqlite3
from datetime import date
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from database import get_connection, init_db

# ---------------------------------------------------------------- config
# Set ADMIN_API_KEY in your environment. The default is for local dev only.
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "dev-key-change-me")

app = FastAPI(title="Student Attendance & Leave Engine", version="2.0")
init_db()


# ---------------------------------------------------------- dependencies
def get_db():
    """One connection per request, always closed, even if the endpoint raises."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def require_admin(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Reject requests that lack a valid X-API-Key header."""
    supplied = (x_api_key or "").encode()
    if not secrets.compare_digest(supplied, ADMIN_API_KEY.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def ensure_student(conn: sqlite3.Connection, student_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Student ID not found")
    return row


# -------------------------------------------------------------- schemas
class Base(BaseModel):
    # Trim leading/trailing whitespace on every string field.
    model_config = ConfigDict(str_strip_whitespace=True)


class StudentCreate(Base):
    student_id: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=100)


class LeaveApply(Base):
    student_id: str = Field(min_length=1, max_length=30)
    reason: str = Field(min_length=1, max_length=500)
    leave_date: date  # parsed and validated as YYYY-MM-DD

    @field_validator("leave_date")
    @classmethod
    def not_in_past(cls, v: date) -> date:
        if v < date.today():
            raise ValueError("leave_date cannot be in the past")
        return v


class LeaveStatusUpdate(Base):
    request_id: int = Field(gt=0)
    status: Literal["APPROVED", "REJECTED"]


class AttendanceMark(Base):
    student_id: str = Field(min_length=1, max_length=30)
    attendance_date: date
    status: Literal["PRESENT", "ABSENT", "LATE", "EXCUSED"]

    @field_validator("attendance_date")
    @classmethod
    def not_in_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("attendance_date cannot be in the future")
        return v


# ------------------------------------------------------------ public API
@app.get("/")
def home():
    return {"status": "Active", "message": "Attendance & Leave Engine Running"}


@app.post("/students/", status_code=201)
def create_student(student: StudentCreate, conn: sqlite3.Connection = Depends(get_db)):
    try:
        conn.execute(
            "INSERT INTO students (student_id, name, department) VALUES (?, ?, ?)",
            (student.student_id, student.name, student.department),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Student ID already exists")
    return {"message": "Student registered successfully"}


@app.get("/students/{student_id}")
def get_student(student_id: str, conn: sqlite3.Connection = Depends(get_db)):
    return dict(ensure_student(conn, student_id))


@app.post("/leave/apply", status_code=201)
def apply_leave(leave: LeaveApply, conn: sqlite3.Connection = Depends(get_db)):
    ensure_student(conn, leave.student_id)
    try:
        cur = conn.execute(
            "INSERT INTO leave_requests (student_id, reason, leave_date) VALUES (?, ?, ?)",
            (leave.student_id, leave.reason, leave.leave_date.isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="A leave request for this student and date already exists",
        )
    return {"message": "Leave application submitted successfully", "request_id": cur.lastrowid}


@app.get("/students/{student_id}/attendance")
def get_attendance(student_id: str, conn: sqlite3.Connection = Depends(get_db)):
    ensure_student(conn, student_id)
    rows = conn.execute(
        "SELECT attendance_date, status FROM attendance "
        "WHERE student_id = ? ORDER BY attendance_date DESC",
        (student_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/students/{student_id}/summary")
def get_student_summary(student_id: str, conn: sqlite3.Connection = Depends(get_db)):
    student = ensure_student(conn, student_id)

    leave = conn.execute(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(status = 'APPROVED'), 0) AS approved, "
        "COALESCE(SUM(status = 'REJECTED'), 0) AS rejected, "
        "COALESCE(SUM(status = 'PENDING'), 0) AS pending "
        "FROM leave_requests WHERE student_id = ?",
        (student_id,),
    ).fetchone()

    att = conn.execute(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(status = 'PRESENT'), 0) AS present, "
        "COALESCE(SUM(status = 'LATE'), 0) AS late, "
        "COALESCE(SUM(status = 'ABSENT'), 0) AS absent, "
        "COALESCE(SUM(status = 'EXCUSED'), 0) AS excused "
        "FROM attendance WHERE student_id = ?",
        (student_id,),
    ).fetchone()

    # Excused days are left out of the denominator; late counts as attended.
    countable = att["total"] - att["excused"]
    attended = att["present"] + att["late"]
    percentage = round(attended / countable * 100, 2) if countable else None

    return {
        "student_id": student_id,
        "name": student["name"],
        "leave": {
            "total_requests": leave["total"],
            "approved": leave["approved"],
            "rejected": leave["rejected"],
            "pending": leave["pending"],
        },
        "attendance": {
            "days_recorded": att["total"],
            "present": att["present"],
            "late": att["late"],
            "absent": att["absent"],
            "excused": att["excused"],
            "attendance_percentage": percentage,  # null until data exists
        },
    }


# ------------------------------------------------------------- admin API
@app.get("/admin/pending-requests", dependencies=[Depends(require_admin)])
def view_pending_requests(conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute(
        "SELECT * FROM leave_requests WHERE status = 'PENDING' ORDER BY leave_date"
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/admin/update-leave", dependencies=[Depends(require_admin)])
def update_leave(action: LeaveStatusUpdate, conn: sqlite3.Connection = Depends(get_db)):
    # The status = 'PENDING' guard makes decisions final and detects bad IDs.
    cur = conn.execute(
        "UPDATE leave_requests SET status = ? WHERE id = ? AND status = 'PENDING'",
        (action.status, action.request_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(
            status_code=404, detail="Request not found or already processed"
        )
    return {"message": f"Leave request {action.request_id} marked as {action.status}"}


@app.post("/admin/attendance", dependencies=[Depends(require_admin)])
def mark_attendance(record: AttendanceMark, conn: sqlite3.Connection = Depends(get_db)):
    ensure_student(conn, record.student_id)
    # Upsert: re-marking the same student and date corrects the record.
    conn.execute(
        "INSERT INTO attendance (student_id, attendance_date, status) VALUES (?, ?, ?) "
        "ON CONFLICT (student_id, attendance_date) DO UPDATE SET status = excluded.status",
        (record.student_id, record.attendance_date.isoformat(), record.status),
    )
    conn.commit()
    return {"message": "Attendance recorded"}