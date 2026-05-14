# from openai import OpenAI

# client = OpenAI(
#   api_key="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"
# )

# response = client.responses.create(
#   model="gpt-5.4-mini",
#   input="write a haiku about ai",
#   store=True,
# )

# print(response.output_text);


# pyrefly: ignore [missing-import]
from openai import OpenAI

client = OpenAI(
    api_key="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"
)

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "user", "content": "write a haiku about ai"}
    ]
)

print(response.choices[0].message.content)


# from openai import OpenAI

# client = OpenAI(
#     api_key="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"
# )

# # List all available models
# models = client.models.list()

# print("\nAvailable Models:\n")

# for model in models.data:
#     print(model.id)