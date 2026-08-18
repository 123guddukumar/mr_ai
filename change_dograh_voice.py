import urllib.request
import json
import sys

# Dograh credentials and target workflow
API_KEY = 'dgr_CSB8YMH-rz5tl1piaWGgl93757B54rluy_zk3u_I0D4'
BASE = 'https://diinvoicepilot.duckdns.org'
DOGRAH_WORKFLOW_ID = 3

def change_voice(new_voice_id):
    # Fetch current config to preserve extra fields if any
    print("Connecting to Dograh API...")
    req = urllib.request.Request(
        BASE + f'/api/v1/workflow/fetch/{DOGRAH_WORKFLOW_ID}',
        headers={'X-API-Key': API_KEY}
    )
    try:
        r = urllib.request.urlopen(req, timeout=15)
        full = json.loads(r.read())
    except Exception as e:
        print("Error fetching current workflow config:", e)
        return

    current_wc = full.get('workflow_configurations', {})
    pipeline = current_wc.get('model_configuration_v2_override', {}).get('byok', {}).get('pipeline', {})
    
    llm = pipeline.get('llm', {})
    stt = pipeline.get('stt', {})
    
    # Real unmasked API keys to prevent masked values (***) from getting saved
    llm_key = "sk-mr-ai-voice-routing"
    stt_key = "5d3770e0a1b4aa755f6d799839bb62ba5561a868"
    tts_key = "sk_cd1566bdbc7c80d5295f6340039775a8a1badff28ce0961b"

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
                            "base_url": "https://vectorize.diintech.com/api/agents"
                        },
                        "tts": {
                            "provider": "elevenlabs",
                            "api_key": tts_key,
                            "voice": new_voice_id,
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

    print(f"Updating workflow voice configuration with ID: {new_voice_id}...")
    body = json.dumps(update_payload).encode('utf-8')
    req_update = urllib.request.Request(
        BASE + f'/api/v1/workflow/{DOGRAH_WORKFLOW_ID}',
        data=body,
        headers={
            'X-API-Key': API_KEY,
            'Content-Type': 'application/json'
        },
        method='PUT'
    )
    
    try:
        r2 = urllib.request.urlopen(req_update, timeout=15)
        print("Update successful in draft config.")
        
        # Publish changes to make it live
        print("Publishing new version...")
        publish_req = urllib.request.Request(
            BASE + f'/api/v1/workflow/{DOGRAH_WORKFLOW_ID}/publish',
            data=b'',
            headers={
                'X-API-Key': API_KEY,
                'Content-Type': 'application/json'
            },
            method='POST'
        )
        r_pub = urllib.request.urlopen(publish_req, timeout=15)
        pub_result = json.loads(r_pub.read())
        print(f"SUCCESS! Published active version: {pub_result.get('version_number')}")
    except Exception as e:
        print("Update or Publish failed:", e)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        voice_id = sys.argv[1].strip()
    else:
        voice_id = input("Enter new ElevenLabs Voice ID: ").strip()
    
    if voice_id:
        change_voice(voice_id)
    else:
        print("No voice ID provided.")
