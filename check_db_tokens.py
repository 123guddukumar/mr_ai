from app.core.database import get_session_local
from app.core.models import Agent, Client
import json

SessionLocal = get_session_local()
db = SessionLocal()
try:
    agents = db.query(Agent).all()
    print(f"--- Agents ---")
    for a in agents:
        print(f"Agent ID: {a.agent_id}, Client ID: {a.client_id}, Name: {a.name}")
        
    print(f"\n--- Clients ---")
    clients = db.query(Client).all()
    for c in clients:
        # Don't print full token for security, just preview
        token_preview = (c.token[:10] + "...") if c.token else "None"
        print(f"Client ID: {c.client_id}, Name: {c.name}, Token Preview: {token_preview}")
finally:
    db.close()
