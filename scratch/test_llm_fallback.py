import asyncio
import os
import sys

# Add current dir to python path
sys.path.append(os.getcwd())

from app.services.llm import generate_simple_response, _runtime
from app.core.config import settings

async def main():
    # Configure provider to Groq
    _runtime["provider"] = "groq"
    _runtime["model"] = "llama-3.3-70b-versatile"
    
    # 1. First, try with a garbage Groq API key to force an error.
    # It should fall back to Gemini (using settings.GEMINI_API_KEY) or OpenAI (using settings.OPENAI_API_KEY).
    _runtime["api_key"] = "bad-key-to-trigger-fallback-12345"
    
    print("Testing generate_simple_response with bad Groq key...")
    try:
        response = await generate_simple_response("Say 'Fallback worked!' in exactly three words.", "You are a helpful assistant.")
        print("Response received:", response)
    except Exception as e:
        print("Test failed with exception:", e)

if __name__ == "__main__":
    asyncio.run(main())
