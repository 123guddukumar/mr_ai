import httpx
import asyncio

keys = [
    "AIzaSyAPpBTJ_y9TPUuBjg2XuCwNkENeucn1R8Y",
    "AIzaSyCIMExnXZhO3FGexRvPFucub2ykZK6kX_o",
    "AIzaSyAiaGNaTV6VIDElL2cf_ok5-me0fR8vpAI"
]

async def test_keys():
    for idx, key in enumerate(keys):
        print(f"\nTesting Key {idx+1}: {key[:10]}...")
        # We will try both gemini-1.5-flash and gemini-2.5-flash
        for model in ["gemini-1.5-flash", "gemini-2.5-flash"]:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            payload = {
                "contents": [{"parts": [{"text": "Hello, how are you?"}]}],
                "generationConfig": {"maxOutputTokens": 20}
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.post(url, json=payload)
                    if r.status_code == 200:
                        print(f"  Model {model}: SUCCESS! Response: {r.json()['candidates'][0]['content']['parts'][0]['text'].strip()}")
                    else:
                        print(f"  Model {model}: FAILED (Status {r.status_code}). Response: {r.text}")
            except Exception as e:
                print(f"  Model {model}: ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_keys())
