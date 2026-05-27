import os
from groq import Groq

# Initialize client using environment variable
client = Groq(
    api_key=os.getenv("GROQ_API_KEY", "")
)

try:
    chat_completion = client.chat.completions.create(
        messages=[
            {
                "role": "user",
                "content": "Explain the importance of fast language models in one sentence.",
            }
        ],
        model="llama-3.3-70b-versatile",
    )

    print("✅ Groq API Working Successfully!")
    print("Response:", chat_completion.choices[0].message.content)

except Exception as e:
    print("❌ Error:")
    print(str(e))