import os
from sqlalchemy import create_engine, text

db_url = None
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.startswith("DATABASE_URL="):
                db_url = line.split("=", 1)[1].strip().strip('"').strip("'")

if not db_url:
    print("DATABASE_URL not found in .env")
    exit(1)

print(f"Connecting to {db_url.split('@')[-1]}...")
engine = create_engine(db_url)

with engine.connect() as conn:
    print("Fixing social_contents table...")
    try:
        # For Postgres
        conn.execute(text("ALTER TABLE social_contents ALTER COLUMN media_url TYPE TEXT;"))
        conn.execute(text("ALTER TABLE social_contents ALTER COLUMN title TYPE VARCHAR(500);"))
        conn.commit()
        print("Success: Updated social_contents table.")
    except Exception as e:
        print(f"Error: {e}")
