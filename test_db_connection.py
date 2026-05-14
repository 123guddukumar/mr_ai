import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

def test_connection():
    url = os.getenv("DATABASE_URL")
    if not url or "[YOUR_PASSWORD]" in url:
        print("Error: Please update DATABASE_URL in .env with your actual password.")
        return

    print(f"Testing connection to: {url.split('@')[-1]}")
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            print("Success! Database is connected.")
    except Exception as e:
        print(f"Connection Failed: {e}")

if __name__ == "__main__":
    test_connection()
