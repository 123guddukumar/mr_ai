"""
Dograh Auto-Provisioning Service
================================
Automatically creates and manages Dograh voice agent workflows
when agents are created in our system.

Key insight: We embed agent_id in the system prompt of each Dograh 
workflow node. Our LLM endpoint parses this to route to the correct agent.

This means:
- ONE Dograh base URL for ALL agents:  https://vectorize.diintech.com/api/agents
- ONE API key for ALL agents:          (any fixed key)
- PER-AGENT:                           Dograh workflow with agent_id in prompt
- ZERO manual Dograh config per agent! (auto-created when agent is created)
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Optional

logger = logging.getLogger(__name__)

DOGRAH_API_KEY = "dgr_CSB8YMH-rz5tl1piaWGgl93757B54rluy_zk3u_I0D4"
DOGRAH_BASE_URL = "https://diinvoicepilot.duckdns.org"
OUR_LLM_BASE_URL = "https://vectorize.diintech.com/api/agents"
OUR_LLM_API_KEY = "sk-mr-ai-voice-routing"  # fixed key, routing via model field

AGENT_ID_TAG_TEMPLATE = "\n\n<!-- agent_id: {agent_id} -->"

# Default minimal workflow definition for new voice agents
def build_default_workflow(agent_id: str, agent_name: str, system_prompt: str) -> dict:
    """Build a minimal Dograh workflow definition for a new agent."""
    tagged_prompt = system_prompt + AGENT_ID_TAG_TEMPLATE.format(agent_id=agent_id)
    
    return {
        "nodes": [
            {
                "id": "start-1",
                "type": "startCall",
                "position": {"x": 175, "y": 60},
                "data": {
                    "name": "Start",
                    "prompt": tagged_prompt,
                    "allow_interrupt": True,
                    "wait_for_user_response": True,
                    "add_global_prompt": False
                }
            },
            {
                "id": "agent-1",
                "type": "agentNode",
                "position": {"x": 175, "y": 300},
                "data": {
                    "name": "Main Conversation",
                    "prompt": tagged_prompt,
                    "allow_interrupt": True,
                    "wait_for_user_response": True,
                    "add_global_prompt": False
                }
            },
            {
                "id": "end-1",
                "type": "endCall",
                "position": {"x": 175, "y": 550},
                "data": {
                    "name": "End",
                    "prompt": "Thank the user and say goodbye."
                }
            }
        ],
        "edges": [
            {
                "id": "edge-start-agent",
                "source": "start-1",
                "target": "agent-1",
                "data": {
                    "label": "Continue",
                    "condition": "The conversation continues"
                }
            },
            {
                "id": "edge-agent-end",
                "source": "agent-1",
                "target": "end-1",
                "data": {
                    "label": "End",
                    "condition": "The user wants to end the conversation or the issue is resolved"
                }
            }
        ]
    }


def _dograh_request(method: str, path: str, payload: Optional[dict] = None) -> dict:
    """Make an authenticated request to the Dograh API."""
    url = DOGRAH_BASE_URL + path
    body = json.dumps(payload).encode("utf-8") if payload else None
    headers = {
        "X-API-Key": DOGRAH_API_KEY,
        "Content-Type": "application/json"
    }
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=15)
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        logger.error(f"Dograh API {method} {path} failed {e.code}: {err_body[:300]}")
        raise


def provision_dograh_agent(agent_id: str, agent_name: str, system_prompt: str) -> Optional[dict]:
    """
    Auto-create a Dograh workflow for a new agent.
    
    Returns: dict with {dograh_workflow_id, dograh_workflow_uuid} or None on failure
    """
    try:
        workflow_def = build_default_workflow(agent_id, agent_name, system_prompt)
        payload = {
            "name": agent_name,
            "workflow_definition": workflow_def
        }
        result = _dograh_request("POST", "/api/v1/workflow/create/definition", payload)
        dograh_id = result.get("id")
        dograh_uuid = result.get("workflow_uuid")
        logger.info(f"Dograh workflow created for agent {agent_id}: id={dograh_id} uuid={dograh_uuid}")
        return {"dograh_workflow_id": dograh_id, "dograh_workflow_uuid": dograh_uuid}
    except Exception as e:
        logger.error(f"Failed to provision Dograh agent for {agent_id}: {e}")
        return None


def sync_agent_prompt_to_dograh(agent_id: str, dograh_workflow_id: int, system_prompt: str) -> bool:
    """
    Update an existing Dograh workflow's node prompts with the agent_id tag.
    Call this when agent prompt is updated.
    """
    try:
        # Fetch current workflow
        current = _dograh_request("GET", f"/api/v1/workflow/fetch/{dograh_workflow_id}")
        nodes = current.get("workflow_definition", {}).get("nodes", [])
        edges = current.get("workflow_definition", {}).get("edges", [])
        
        import re
        tag = AGENT_ID_TAG_TEMPLATE.format(agent_id=agent_id)
        updated_nodes = []
        for node in nodes:
            n = json.loads(json.dumps(node))
            if "data" in n and "prompt" in n["data"]:
                # Remove old tag if present, add fresh
                p = re.sub(r'\n*<!--\s*agent_id:\s*[a-f0-9]+\s*-->', '', n["data"]["prompt"])
                n["data"]["prompt"] = p + tag
            updated_nodes.append(n)
        
        _dograh_request("PUT", f"/api/v1/workflow/{dograh_workflow_id}", {
            "workflow_definition": {"nodes": updated_nodes, "edges": edges}
        })
        logger.info(f"Synced agent_id tag for agent {agent_id} to Dograh workflow {dograh_workflow_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to sync agent prompt to Dograh: {e}")
        return False


def embed_agent_id_in_existing_dograh_workflow(dograh_workflow_id: int, agent_id: str) -> bool:
    """
    One-time fix: embed agent_id in an existing Dograh workflow's prompts.
    Use this to fix manually-created Dograh workflows.
    """
    return sync_agent_prompt_to_dograh(agent_id, dograh_workflow_id, "")


def sync_voice_config_only(voice_id: str, elevenlabs_api_key: str) -> bool:
    """
    Dynamically update the TTS voice configuration of the shared Dograh workflow
    right before the call starts.
    """
    try:
        # Fetch current config to preserve system parameters
        current = _dograh_request("GET", "/api/v1/workflow/fetch/3")
        current_wc = current.get('workflow_configurations', {})
        pipeline = current_wc.get('model_configuration_v2_override', {}).get('byok', {}).get('pipeline', {})
        
        llm = pipeline.get('llm', {})
        stt = pipeline.get('stt', {})
        
        # Real unmasked API keys to prevent placeholder stars from corrupting connection
        llm_key = "sk-mr-ai-voice-routing"
        stt_key = "5d3770e0a1b4aa755f6d799839bb62ba5561a868"
        
        update_payload = {
            "workflow_configurations": {
                "model_configuration_v2_override": {
                    "version": 2,
                    "mode": "byok",
                    "byok": {
                        "mode": "pipeline",
                        "pipeline": {
                            "llm": {
                                "provider": "openai",
                                "api_key": llm_key,
                                "model": llm.get('model', 'd6b54c1e63290e77'),
                                "base_url": llm.get('base_url', 'https://vectorize.diintech.com/api/agents')
                            },
                            "tts": {
                                "provider": "elevenlabs",
                                "api_key": elevenlabs_api_key,
                                "voice": voice_id,
                                "speed": 1.0,
                                "model": "eleven_turbo_v2",
                                "base_url": "https://api.elevenlabs.io"
                            },
                            "stt": {
                                "provider": "deepgram",
                                "api_key": stt_key,
                                "model": stt.get('model', 'nova-3-general'),
                                "language": stt.get('language', 'multi')
                            }
                        }
                    }
                }
            }
        }
        
        _dograh_request("PUT", "/api/v1/workflow/3", update_payload)
        _dograh_request("POST", "/api/v1/workflow/3/publish")
        logger.info(f"Successfully synced voice config to Dograh: voice_id={voice_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to sync voice config only to Dograh: {e}")
        return False


if __name__ == "__main__":
    # Test: embed agent_id in existing workflow 3
    success = embed_agent_id_in_existing_dograh_workflow(3, "d6b54c1e63290e77")
    print("Test result:", success)
