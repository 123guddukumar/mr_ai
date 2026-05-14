from app.core.database import get_session_local
from app.core.models import Agent, Client
import json

SessionLocal = get_session_local()
db = SessionLocal()
try:
    agents = db.query(Agent).all()
    print(f"Found {len(agents)} agents in database.")
    for a in agents:
        print(f"Agent ID: {a.agent_id}, Client ID: {a.client_id}, Name: {a.name}")
        
    clients = db.query(Client).all()
    print(f"\nFound {len(clients)} clients in database.")
    for c in clients:
        print(f"Client ID: {c.client_id}, Name: {c.name}")
finally:
    db.close()
