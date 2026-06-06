import json
import os
from datetime import datetime

jobs_file = os.path.join(os.getcwd(), "vector_store", "ext_jobs.json")
if os.path.exists(jobs_file):
    mtime = datetime.fromtimestamp(os.path.getmtime(jobs_file))
    print(f"File modified: {mtime}")
    with open(jobs_file, encoding="utf-8") as f:
        jobs = json.load(f)
    j = jobs.get("job-27acaee2da9fc0ae")
    if j:
        print(json.dumps(j, indent=2, ensure_ascii=True))
    else:
        print("Job not found")
else:
    print("Jobs file not found")
