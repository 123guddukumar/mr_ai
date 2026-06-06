import json

try:
    with open('vector_store/ext_jobs.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Sort by created_at descending
    sorted_jobs = sorted(
        data.values(),
        key=lambda j: j.get('created_at', ''),
        reverse=True
    )
    
    print("--- TOP 5 NEWEST JOBS ---")
    for v in sorted_jobs[:5]:
        print(f"Job ID: {v.get('job_id')}")
        print(f"  Status: {v.get('status')}")
        print(f"  Progress %: {v.get('progress_pct')}")
        print(f"  Message: {v.get('progress_msg')}")
        print(f"  Video URL: {v.get('video_url')}")
        print(f"  Created At: {v.get('created_at')}")
        print("-" * 30)
except Exception as e:
    print(f"Error: {e}")
