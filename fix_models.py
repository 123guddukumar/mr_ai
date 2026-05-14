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
            # Update to gemini-2.5-flash as requested by user
            if cfg.get('model') == 'gemini-1.5-flash' or cfg.get('model') is None:
                cfg['model'] = 'gemini-2.5-flash'
                a.system_config_json = json.dumps(cfg)
                count += 1
        except:
            continue
    db.commit()
    print(f"Updated {count} agents to gemini-2.5-flash.")
finally:
    db.close()
