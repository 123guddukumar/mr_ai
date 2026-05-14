import logging
import json
import secrets
from datetime import datetime
from typing import List, Optional, Dict
from sqlalchemy.orm import Session
from app.core.models import DataStore, DataStoreSource, Agent, AgentKnowledgeSource

logger = logging.getLogger(__name__)

# ── DataStore Logic ──────────────────────────────────────────────────────────

def create_datastore(client_id: str, name: str, db: Session) -> DataStore:
    ds_id = "ds-" + secrets.token_hex(5)
    ds = DataStore(
        datastore_id=ds_id,
        client_id=client_id,
        name=name,
        created_at=datetime.utcnow()
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds

def get_datastores(client_id: str, db: Session) -> List[DataStore]:
    return db.query(DataStore).filter(DataStore.client_id == client_id).order_by(DataStore.created_at.desc()).all()

def delete_datastore(ds_id: str, client_id: str, db: Session) -> bool:
    ds = db.query(DataStore).filter(DataStore.datastore_id == ds_id, DataStore.client_id == client_id).first()
    if not ds: return False
    db.delete(ds)
    db.commit()
    return True

# ── Agent Logic ──────────────────────────────────────────────────────────────

def create_agent(client_id: str, name: str, db: Session) -> Agent:
    agent_id = "agent-" + secrets.token_hex(5)
    agent = Agent(
        agent_id=agent_id,
        client_id=client_id,
        name=name,
        created_at=datetime.utcnow()
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent

def update_agent(agent_id: str, client_id: str, db: Session, **kwargs) -> Optional[Agent]:
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.client_id == client_id).first()
    if not agent: return None
    
    for key, value in kwargs.items():
        if hasattr(agent, key):
            # If it's a JSON field, ensure it's a string
            if key.endswith('_json') and not isinstance(value, str):
                setattr(agent, key, json.dumps(value))
            else:
                setattr(agent, key, value)
    
    db.commit()
    db.refresh(agent)
    return agent

def get_agents(client_id: str, db: Session) -> List[Agent]:
    return db.query(Agent).filter(Agent.client_id == client_id).order_by(Agent.created_at.desc()).all()

def delete_agent(agent_id: str, client_id: str, db: Session) -> bool:
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.client_id == client_id).first()
    if not agent: return False
    db.delete(agent)
    db.commit()
    return True

# ── Source Deletion Logic ───────────────────────────────────────────────────

def delete_datastore_source(ds_id: str, source_id: int, client_id: str, db: Session) -> bool:
    ds = db.query(DataStore).filter(DataStore.datastore_id == ds_id, DataStore.client_id == client_id).first()
    if not ds: return False
    
    src = db.query(DataStoreSource).filter(DataStoreSource.id == source_id, DataStoreSource.datastore_id == ds_id).first()
    if not src: return False
    
    # Purge from Vector Store
    from app.services.vector_store import get_vector_store
    get_vector_store().purge_by_source(datastore_id=ds_id, source_file=src.source_name)
    
    db.delete(src)
    db.commit()
    return True

def delete_agent_source(agent_id: str, source_id: int, client_id: str, db: Session) -> bool:
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.client_id == client_id).first()
    if not agent: return False
    
    src = db.query(AgentKnowledgeSource).filter(AgentKnowledgeSource.id == source_id, AgentKnowledgeSource.agent_id == agent_id).first()
    if not src: return False
    
    # Purge from Vector Store
    from app.services.vector_store import get_vector_store
    get_vector_store().purge_by_source(agent_id=agent_id, source_file=src.filename)
    
    db.delete(src)
    db.commit()
    return True
