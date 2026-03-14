import os
import json
import datetime

brain_dir = 'C:/Users/LENOVO/.gemini/antigravity/brain/'
history = []

for folder in os.listdir(brain_dir):
    folder_path = os.path.join(brain_dir, folder)
    if os.path.isdir(folder_path):
        mtime = os.path.getmtime(folder_path)
        dt = datetime.datetime.fromtimestamp(mtime)
        
        # Only keep March 1 to 13
        if dt.year == 2026 and dt.month == 3 and 1 <= dt.day <= 13:
            title = folder
            summary = "Session active during this period."
            
            # Read metadata
            for md_file in ["walkthrough.md.metadata.json", "implementation_plan.md.metadata.json", "task.md.metadata.json"]:
                meta_path = os.path.join(folder_path, md_file)
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            if "Summary" in data:
                                summary = data["Summary"]
                                title = summary.split('\n')[0][:50]
                        break
                    except:
                        pass
                
            history.append({
                "folder": folder,
                "date": dt.strftime('%Y-%m-%d'),
                "summary": summary
            })

history.sort(key=lambda x: x['date'])

with open('C:/Users/LENOVO/.gemini/antigravity/brain/131b02bf-252c-438f-9716-6a88ec2918b4/history.json', 'w', encoding='utf-8') as f:
    json.dump(history, f, indent=2)
