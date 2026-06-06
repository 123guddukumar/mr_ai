import json

with open("vector_store/ext_jobs.json", "r", encoding="utf-8") as f:
    jobs = json.load(f)

job_id = "job-85a418ae52ea44bc"
if job_id in jobs:
    job = jobs[job_id]
    print(f"Job ID: {job_id}")
    print(f"Status: {job.get('status')}")
    print(f"Created At: {job.get('created_at')}")
    print(f"Subtopic: {job.get('subtopic_name')}")
    print(f"Images Received: {job.get('images_received')}")
    print(f"Videos Received: {job.get('videos_received')}")
    print(f"Error: {job.get('error')}")
else:
    print(f"Job {job_id} not found in database.")
