import json
import os

log_path = r"C:\Users\LENOVO\.gemini\antigravity-ide\brain\80c95133-826f-4a93-9948-303ebbc8ac8a\.system_generated\logs\transcript.jsonl"
if not os.path.exists(log_path):
    print("Log file not found.")
    exit(1)

with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
    for line in f:
        try:
            data = json.loads(line)
            tool_calls = data.get("tool_calls", [])
            for tc in tool_calls:
                args = tc.get("args", {})
                target_file = args.get("TargetFile", "")
                if target_file and "extension.py" in target_file:
                    print(f"=== Step {data.get('step_index')} Tool: {tc.get('name')} ===")
                    print(json.dumps(args, indent=2))
                    print("-" * 50)
        except Exception as e:
            pass
