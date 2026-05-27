from app.core.database import get_session_local
from app.core.models import Client, Exam

SessionLocal = get_session_local()
db = SessionLocal()
try:
    print("--- CLIENTS ---")
    for c in db.query(Client).all():
        print(f"Client ID: {c.client_id}, Name: {c.name}, Token: {c.token}")
    print("\n--- EXAMS ---")
    for e in db.query(Exam).all():
        print(f"Exam ID: {e.exam_id}, Name: {e.name}, Client ID: {e.client_id}")
finally:
    db.close()
