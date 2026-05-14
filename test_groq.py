from groq import Groq

client = Groq(api_key="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")

chat_completion = client.chat.completions.create(
    messages=[
        {
            "role": "user",
            "content": "Explain the importance of fast language models",
        }
    ],
    model="llama-3.3-70b-versatile",
)

print(chat_completion.choices[0].message.content)

# from groq import Groq

# client = Groq(api_key="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")

# models = client.models.list()

# for model in models.data:
#     print(model.id)