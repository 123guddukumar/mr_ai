from app.core.database import get_session_local
from app.core.models import Agent
import json

SessionLocal = get_session_local()
db = SessionLocal()
try:
    agent = db.query(Agent).filter(Agent.agent_id == 'da3243d9babb9387').first()
    if agent:
        print(f"DEBUG_NAME: {agent.name}")
        print(f"DEBUG_CONFIG: {agent.system_config_json}")
        print(f"DEBUG_PERS: {agent.personality}")
    else:
        print("Agent not found")
finally:
    db.close()
