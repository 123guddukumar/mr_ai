import sys
import os

# Add the current directory to sys.path so we can import 'app'
sys.path.append(os.getcwd())

from app.core.admin import create_admin
from app.core.database import get_session_local, init_db

def main():
    print("🚀 VectorizeAI — Super Admin Creation Tool")
    print("-" * 40)
    
    # Initialize DB (add new columns if needed)
    print("📦 Synchronizing database schema...")
    try:
        init_db()
        print("✅ Database schema is up to date.")
    except Exception as e:
        print(f"⚠️ Warning during DB init: {e}")

    print("\n📝 Enter account details:")
    username = input("Enter Super Admin Username (e.g. admin): ").strip()
    email = input("Enter Super Admin Email: ").strip()
    password = input("Enter Super Admin Password: ").strip()
    
    if not username or not email or not password:
        print("\n❌ Error: All fields are required.")
        return

    db = get_session_local()() # Create session instance
    try:
        result = create_admin(
            username=username,
            password=password,
            is_super=True,
            email=email,
            db=db
        )
        
        if result:
            print(f"\n✅ Success! Super Admin '{username}' created.")
            print(f"🔗 Login at: http://localhost:8000/admin-login")
        else:
            print(f"\n❌ Error: Admin with username '{username}' already exists.")
            
    except Exception as e:
        print(f"\n❌ An error occurred: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
