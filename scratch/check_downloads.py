import os
import glob
from pathlib import Path
from datetime import datetime

home = Path.home()
downloads_dirs = [
    str(home / "Downloads"),
    str(home / "OneDrive" / "Downloads"),
]

print("--- Inspecting Downloads Folders ---")
for ddir in downloads_dirs:
    if os.path.exists(ddir):
        print(f"\nChecking directory: {ddir}")
        files = glob.glob(os.path.join(ddir, "*meta*")) + glob.glob(os.path.join(ddir, "*flow*"))
        if not files:
            print("No files containing 'meta' or 'flow' found.")
            continue
        # Sort by modification time
        files.sort(key=os.path.getmtime, reverse=True)
        for f in files[:15]:
            mtime_epoch = os.path.getmtime(f)
            mtime_utc = datetime.utcfromtimestamp(mtime_epoch)
            mtime_local = datetime.fromtimestamp(mtime_epoch)
            size = os.path.getsize(f)
            print(f"File: {os.path.basename(f)}")
            print(f"  Size: {size} bytes")
            print(f"  Mtime (UTC): {mtime_utc}")
            print(f"  Mtime (Local): {mtime_local}")
            print(f"  Epoch: {mtime_epoch}")
