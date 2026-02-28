import requests

BASE_URL = "http://localhost:8000/api"
API_KEY  = "mrairag-Qil2qqbSwwz5d0tuEEmxaJfGk29jcUDLAFacOGmO"
HEADERS  = {"X-API-Key": API_KEY}

# 1. Upload a PDF
# with open("document.pdf", "rb") as f:
#     r = requests.post(f"{BASE_URL}/upload",
#                       headers=HEADERS,
#                       files={"file": f})
#     print(r.json())  # {"success": true, "total_chunks": 42, ...}

# 2. Ask a question
r = requests.post(f"{BASE_URL}/ask",
    headers={**HEADERS, "Content-Type": "application/json"},
    json={"question": "What are the key findings?", "top_k": 5})
print(r.json()["answer"])

# 3. Ingest a YouTube video
r = requests.post(f"{BASE_URL}/ingest-youtube",
    headers={**HEADERS, "Content-Type": "application/json"},
    json={"url": "https://youtu.be/dQw4w9WgXcQ"})
print(r.json())

# 4. Scrape a website
r = requests.post(f"{BASE_URL}/ingest-url",
    headers={**HEADERS, "Content-Type": "application/json"},
    json={"url": "https://en.wikipedia.org/wiki/AI"})
print(r.json())