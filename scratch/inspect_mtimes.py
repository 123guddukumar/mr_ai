import os
from datetime import datetime

work_dir = r"c:\Users\LENOVO\Downloads\mr_ai_rag_v2\mr_ai_rag_v2\uploads\social\ext_work_job-c6ee"
if os.path.exists(work_dir):
    for f in os.listdir(work_dir):
        path = os.path.join(work_dir, f)
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        size = os.path.getsize(path)
        print(f"{f:<30} | Size: {size:<10} | Modified: {mtime}")
else:
    print("Directory does not exist")
