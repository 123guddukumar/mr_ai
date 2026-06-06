import os
import sys
from datetime import datetime

# Add app directory to path
sys.path.append(os.getcwd())

from app.routes.extension import resilient_find_file

filename = "meta-img-1-job-85a418ae52ea44bc-Macaulays.jpg"
scene_num = 1
job_id = "job-85a418ae52ea44bc"

print("Calling resilient_find_file...")
result = resilient_find_file(filename, scene_num, job_id, is_video=False)
print("Result:", result)
