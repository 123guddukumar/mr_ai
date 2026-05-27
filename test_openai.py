import os
from openai import OpenAI

# Initialize client using environment variable
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY", "")
)

# Check account usage/quota by making a small request
try:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "user", "content": "hello"}
        ],
        max_tokens=5
    )

    print("✅ OpenAI API Working Successfully!")
    print("Response:", response.choices[0].message.content)

except Exception as e:
    print("❌ Error:")
    print(str(e))