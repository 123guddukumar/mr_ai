import json
import os
from sqlalchemy.orm import Session
from app.core.database import get_engine
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

def test():
    # Load jobs
    JOBS_FILE = os.path.join(os.getcwd(), "vector_store", "ext_jobs.json")
    print("Jobs file exists:", os.path.exists(JOBS_FILE))
    if os.path.exists(JOBS_FILE):
        with open(JOBS_FILE, encoding='utf-8') as f:
            jobs = json.loads(f.read())
            print("Loaded jobs count:", len(jobs))
            for jid, job in jobs.items():
                print(f"Job: {jid}, client_id: {job.get('client_id')}, status: {job.get('status')}")

    # Query DB for clients
    from app.core.models import Client
    engine = get_engine()
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        clients = db.query(Client).all()
        print("\nRegistered Clients:")
        for c in clients:
            print(f"Client Name: {c.name}, ID: {c.client_id}, Email: {c.email}, Token: {c.token}")
    finally:
        db.close()

if __name__ == "__main__":
    test()
