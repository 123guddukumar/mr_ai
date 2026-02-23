import requests
import os

API_KEY = "AIzaSyAPpBTJ_y9TPUuBjg2XuCwNkENeucn1R8Y"   # <-- apni key yaha daalo

def check_available_models():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    
    response = requests.get(url)

    if response.status_code != 200:
        print("❌ Error:", response.status_code, response.text)
        return

    data = response.json()
    models = data.get("models", [])

    print("\n✅ Available Models (supporting generateContent):\n")

    for model in models:
        name = model.get("name", "")
        methods = model.get("supportedGenerationMethods", [])
        
        if "generateContent" in methods:
            print("•", name)

if __name__ == "__main__":
    check_available_models()
    