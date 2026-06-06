import json
import os

JOBS_FILE = r"c:\Users\LENOVO\Downloads\mr_ai_rag_v2\mr_ai_rag_v2\vector_store\ext_jobs.json"

if os.path.exists(JOBS_FILE):
    with open(JOBS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"Total jobs: {len(data)}")
    for jid, job in data.items():
        print(f"ID: {jid}")
        print(f"  Status: {job.get('status')}")
        print(f"  Progress: {job.get('progress_pct')}% - {job.get('progress_msg')}")
        print(f"  BGM URL: {job.get('bgm_url')}")
        print(f"  Created At: {job.get('created_at')}")
        print(f"  Images Received: {job.get('images_received')}")
        print(f"  Videos Received: {job.get('videos_received')}")
        print("-" * 50)
else:
    print("Jobs file not found")
