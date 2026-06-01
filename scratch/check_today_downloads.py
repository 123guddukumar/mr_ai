import os
import glob
from pathlib import Path
from datetime import datetime

home = Path.home()
search_dirs = [
    str(home / "Downloads"),
    str(home / "OneDrive" / "Downloads"),
    str(home / "OneDrive" / "Desktop"),
    str(home / "Desktop"),
    os.path.join(os.environ.get("USERPROFILE", ""), "Downloads"),
    os.path.join(os.environ.get("USERPROFILE", ""), "OneDrive", "Downloads")
]

print("--- Comprehensive Search for Today's Downloads ---")
today_str = "2026-05-28"

for ddir in search_dirs:
    if not ddir or not os.path.exists(ddir):
        continue
    print(f"\nSearching folder: {ddir}")
    # List all files in the folder
    try:
        files = os.listdir(ddir)
        matched_files = []
        for f in files:
            path = os.path.join(ddir, f)
            if os.path.isdir(path):
                continue
            
            # Check if file has meta or flow in name OR matches the format
            is_meta = "meta" in f.lower() or "flow" in f.lower() or f.endswith(".jpg") or f.endswith(".mp4")
            
            mtime_epoch = os.path.getmtime(path)
            mtime_local = datetime.fromtimestamp(mtime_epoch)
            mtime_local_str = mtime_local.strftime("%Y-%m-%d")
            
            if mtime_local_str == today_str and is_meta:
                matched_files.append((f, mtime_local, os.path.getsize(path)))
                
        if matched_files:
            matched_files.sort(key=lambda x: x[1], reverse=True)
            for name, mtime, size in matched_files:
                print(f"  File: {name}")
                print(f"    Size: {size} bytes")
                print(f"    Mtime: {mtime}")
        else:
            print("  No matching files downloaded today found here.")
    except Exception as e:
        print(f"  Error searching: {e}")
