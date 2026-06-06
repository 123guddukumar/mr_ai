import os
import glob
from pathlib import Path
from datetime import datetime

home = Path.home()
downloads_dirs = [
    str(home / "Downloads"),
    str(home / "OneDrive" / "Downloads"),
    str(home / "OneDrive" / "Desktop"),
    str(home / "Desktop"),
    os.path.join(os.environ.get("USERPROFILE", ""), "Downloads"),
    os.path.join(os.environ.get("USERPROFILE", ""), "OneDrive", "Downloads")
]

unique_dirs = []
for d in downloads_dirs:
    if d:
        p = os.path.abspath(d)
        if os.path.exists(p) and p not in unique_dirs:
            unique_dirs.append(p)

print("Searching in directories:", unique_dirs)
for d in unique_dirs:
    print(f"\n--- Directory: {d} ---")
    files = glob.glob(os.path.join(d, "meta-*"))
    if not files:
        print("No files starting with 'meta-' found.")
        continue
    for f in sorted(files, key=os.path.getmtime, reverse=True)[:20]:
        mtime = datetime.fromtimestamp(os.path.getmtime(f))
        size = os.path.getsize(f)
        print(f"{os.path.basename(f):<50} | Size: {size:<10} | Modified: {mtime}")
