import sys
import os
import asyncio
import logging

sys.path.insert(0, os.getcwd())

# Configure logging to stdout
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

from app.core.database import get_session_local
from app.routes.classroom import generate_pyq_overview

async def run_gen():
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        # Get the latest PYQ set
        from app.core.models import PYQSet
        pyq_set = db.query(PYQSet).order_by(PYQSet.created_at.desc()).first()
        if not pyq_set:
            print("No PYQ set found.")
            return

        print(f"Generating overview for PYQ Set: {pyq_set.name} (ID: {pyq_set.pyq_set_id})...")
        
        # Reset overview generated flag and explanations for testing
        pyq_set.overview_generated = False
        db.commit()
        
        # We call the function
        # Mock client dict with client_id
        client_mock = {"client_id": pyq_set.client_id}
        
        # We will only run for 5 questions to verify if it works or fails
        res = await generate_pyq_overview(pyq_set.pyq_set_id, "English", client_mock, db)
        print("Result:", res)
        
    except Exception as e:
        print("Failure:", e)
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(run_gen())
