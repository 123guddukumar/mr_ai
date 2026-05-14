import requests

API_KEY = "AIzaSyBt32PpStf7-QfIw56RkR9gEdWWSPvPls8"

url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"

payload = {
    "contents": [
        {
            "parts": [
                {
                    "text": "Hello, test message"
                }
            ]
        }
    ]
}

try:
    response = requests.post(url, json=payload, timeout=30)

    print("Status Code:", response.status_code)
    print("Response:\n", response.text)

    if response.status_code == 200:
        print("\n✅ API Key Working Fine")

    elif response.status_code == 429:
        print("\n⚠️ Rate limit / quota exceeded")

    elif response.status_code == 403:
        print("\n❌ API key invalid or permission issue")

    elif response.status_code == 503:
        print("\n⚠️ Gemini service temporarily unavailable")

    else:
        print("\n❓ Some other issue")

except Exception as e:
    print("Error:", str(e))