from sqlalchemy import text
from app.core.database import SessionLocal

def migrate():
    db = SessionLocal()
    try:
        # Add columns to agents table if they don't exist
        db.execute(text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS voice_config_json TEXT DEFAULT '{}'"))
        db.execute(text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS system_config_json TEXT DEFAULT '{}'"))
        db.execute(text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS customization_json TEXT DEFAULT '{}'"))
        db.execute(text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS datastores_json TEXT DEFAULT '[]'"))
        
        # Also check AgentKnowledgeSource if needed
        # db.execute(text("ALTER TABLE agent_knowledge_sources ADD COLUMN IF NOT EXISTS source_type VARCHAR(30)"))
        # db.execute(text("ALTER TABLE agent_knowledge_sources ADD COLUMN IF NOT EXISTS source_name VARCHAR(500)"))
        
        db.commit()
        print("Migration successful")
    except Exception as e:
        print(f"Migration failed: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    migrate()
