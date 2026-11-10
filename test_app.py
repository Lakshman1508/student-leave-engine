import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["ADMIN_API_KEY"] = "k"

from datetime import date, timedelta
from fastapi.testclient import TestClient
from main import app

c = TestClient(app)
H = {"X-API-Key": "k"}
fut = (date.today() + timedelta(days=5)).isoformat()
past = (date.today() - timedelta(days=5)).isoformat()
S = {"student_id": "S001", "name": " Asha ", "department": "CSE"}


def test_all():
    assert c.get("/").status_code == 200
    assert c.post("/students/", json=S).status_code == 201
    assert c.post("/students/", json=S).status_code == 409
    assert c.get("/students/S001").json()["name"] == "Asha"
    assert c.post("/students/", json={**S, "student_id": "S2", "name": "  "}).status_code == 422
    assert c.get("/students/NOPE").status_code == 404

    # leave requests
    L = {"student_id": "S001", "reason": "Medical", "leave_date": fut}
    r = c.post("/leave/apply", json=L)
    assert r.status_code == 201 and r.json()["request_id"] == 1
    assert c.post("/leave/apply", json=L).status_code == 409
    assert c.post("/leave/apply", json={**L, "leave_date": "banana"}).status_code == 422
    assert c.post("/leave/apply", json={**L, "leave_date": past}).status_code == 422
    assert c.post("/leave/apply", json={**L, "reason": ""}).status_code == 422
    assert c.post("/leave/apply", json={**L, "student_id": "NOPE"}).status_code == 404

    # admin authentication
    assert c.get("/admin/pending-requests").status_code == 401
    assert c.get("/admin/pending-requests", headers={"X-API-Key": "bad"}).status_code == 401
    assert len(c.get("/admin/pending-requests", headers=H).json()) == 1
    U = {"request_id": 1, "status": "APPROVED"}
    assert c.post("/admin/update-leave", json=U).status_code == 401
    assert c.post("/admin/update-leave", json=U, headers=H).status_code == 200
    assert c.post("/admin/update-leave", json=U, headers=H).status_code == 404
    bad_id = {"request_id": 999, "status": "APPROVED"}
    assert c.post("/admin/update-leave", json=bad_id, headers=H).status_code == 404
    bad_status = {"request_id": 1, "status": "MAYBE"}
    assert c.post("/admin/update-leave", json=bad_status, headers=H).status_code == 422

    # attendance and summary
    s0 = c.get("/students/S001/summary").json()
    assert s0["attendance"]["attendance_percentage"] is None
    assert s0["leave"]["approved"] == 1

    def mark(d, status):
        body = {"student_id": "S001", "attendance_date": d, "status": status}
        return c.post("/admin/attendance", json=body, headers=H)

    day = lambda n: (date.today() - timedelta(days=n)).isoformat()
    assert mark(day(0), "ABSENT").status_code == 200
    assert mark(day(0), "PRESENT").status_code == 200   # upsert corrects the record
    assert mark(day(1), "ABSENT").status_code == 200
    assert mark(day(2), "EXCUSED").status_code == 200
    assert mark(day(3), "LATE").status_code == 200
    assert mark(fut, "PRESENT").status_code == 422      # future date
    unknown = {"student_id": "NOPE", "attendance_date": day(0), "status": "PRESENT"}
    assert c.post("/admin/attendance", json=unknown, headers=H).status_code == 404
    assert c.post("/admin/attendance", json=unknown).status_code == 401

    att = c.get("/students/S001/attendance").json()
    assert len(att) == 4 and att[0]["attendance_date"] == day(0)
    summary = c.get("/students/S001/summary").json()["attendance"]
    assert summary == {
        "days_recorded": 4, "present": 1, "late": 1,
        "absent": 1, "excused": 1, "attendance_percentage": 66.67,
    }
    assert c.get("/students/NOPE/summary").status_code == 404