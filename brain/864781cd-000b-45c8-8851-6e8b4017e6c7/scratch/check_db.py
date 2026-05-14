import sqlite3
import os

db_path = "app.db"
if not os.path.exists(db_path):
    print("Database not found at app.db")
else:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # SQLite doesn't support ALTER COLUMN, we have to use a trick or just ignore if it's already large.
        # Actually, SQLite String(N) is just TEXT anyway, the N is ignored by SQLite itself!
        # The error 500 might be from SQLAlchemy's validation OR if it's NOT SQLite.
        
        print("Checking table structure...")
        cursor.execute("PRAGMA table_info(social_contents);")
        columns = cursor.fetchall()
        for col in columns:
            print(col)
            
        conn.close()
        print("Done.")
    except Exception as e:
        print(f"Error: {e}")
