import os

work_dir = "uploads/social/ext_work_job-85a4"
if os.path.exists(work_dir):
    print(f"Directory {work_dir} exists.")
    for f in sorted(os.listdir(work_dir)):
        path = os.path.join(work_dir, f)
        print(f"File: {f:<30} | Size: {os.path.getsize(path)}")
else:
    print(f"Directory {work_dir} does NOT exist.")
