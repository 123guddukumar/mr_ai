from app.core.database import get_session_local
from app.core.models import Agent
import json

SessionLocal = get_session_local()
db = SessionLocal()
try:
    agents = db.query(Agent).all()
    count = 0
    for a in agents:
        try:
            cfg = json.loads(a.system_config_json or "{}")
            # Update to gemini-3.5-flash
            current_model = cfg.get('model')
            if current_model in ['gemini-1.5-flash', 'gemini-2.5-flash', None, '']:
                cfg['model'] = 'gemini-3.5-flash'
                a.system_config_json = json.dumps(cfg)
                count += 1
        except Exception as err:
            print(f"Error updating agent {a.agent_id}: {err}")
            continue
    db.commit()
    print(f"Updated {count} agents to gemini-3.5-flash.")
finally:
    db.close()
