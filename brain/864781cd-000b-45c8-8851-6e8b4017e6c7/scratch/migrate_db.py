import asyncio
from sqlalchemy import text
from app.core.database import SessionLocal, engine
from app.core.models import Base

def fix_db():
    print("Connecting to database...")
    with engine.connect() as conn:
        print("Altering table social_contents...")
        try:
            conn.execute(text("ALTER TABLE social_contents ALTER COLUMN media_url TYPE TEXT;"))
            conn.execute(text("ALTER TABLE social_contents ALTER COLUMN title TYPE VARCHAR(500);"))
            conn.commit()
            print("Successfully altered social_contents table.")
        except Exception as e:
            print(f"Error during ALTER TABLE: {e}")

if __name__ == "__main__":
    fix_db()
