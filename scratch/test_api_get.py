import requests
import json

token = "clt-2db63e7fbb785339128218bac891c01c35f09e23d28a018e"
exam_id = "exam-f4bb9287a848ad7f"

print(f"Requesting details for exam: {exam_id}...")
resp = requests.get(
    f"http://localhost:8000/api/classroom/exams/{exam_id}",
    headers={
        "X-App-Token": token
    }
)
print(f"Status Code: {resp.status_code}")
try:
    print(json.dumps(resp.json(), indent=2))
except Exception as e:
    print("Response text:", resp.text)
