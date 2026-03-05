import requests

res = requests.post(
    "http://localhost:8000/api/memory/mem-4d1d927cfe653d78/ask",
    headers={
        "Content-Type": "application/json",
        "X-Client-Token": "clt-23c0afe09ab999ca9ed862eeec56f0c6f53a01d9f15f2cce"
    },
    json={"question": "What is this about?", "top_k": 5}
)
print(res.json()["answer"])