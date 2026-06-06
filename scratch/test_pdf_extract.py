import sys
import os
import json
import re

# Ensure the root of the project is in path
sys.path.insert(0, os.getcwd())

from app.routes.classroom import extract_text_from_pdf
from app.services.llm import generate_simple_response
import asyncio

async def test_pdf():
    pdf_path = "70th BPSC.pdf"
    if not os.path.exists(pdf_path):
        print(f"Error: {pdf_path} not found.")
        return

    print("Reading file contents...")
    with open(pdf_path, "rb") as f:
        contents = f.read()

    print("Extracting text from PDF...")
    text = extract_text_from_pdf(contents, max_pages=30)
    print(f"Extracted text length: {len(text)}")
    if not text.strip():
        print("PDF is scanned (no digital text found)!")
        return

    print("First 500 characters of extracted text:")
    print("-" * 50)
    print(text[:500])
    print("-" * 50)

    # Let's run a single chunk parse to see if the LLM succeeds or fails
    chunk_text = text[:8000]
    print("Sending chunk to generate_simple_response to extract MCQs...")
    parse_prompt = f"""Extract all MCQ (Multiple Choice Questions) from the following exam text chunk.
Return a strict JSON array in this format:
[
  {{
    "question": "Question text here",
    "options": ["Option A", "Option B", "Option C", "Option D"],
    "correct": "Correct option text"
  }}
]
If correct answer is not shown, leave "correct" as empty string.
Do NOT include markdown wrappers, only return the JSON array.

EXAM TEXT CHUNK:
{chunk_text}"""

    try:
        raw_response = await generate_simple_response(parse_prompt, "You are an MCQ extraction expert. Return only valid JSON.")
        print("Raw response from LLM:")
        print(raw_response)
        
        clean = re.sub(r'```json\s*', '', raw_response)
        clean = re.sub(r'\s*```', '', clean).strip()
        first_bracket = clean.find('[')
        last_bracket = clean.rfind(']')
        if first_bracket != -1 and last_bracket != -1:
            clean = clean[first_bracket:last_bracket+1]
        
        questions = json.loads(clean)
        print(f"Successfully parsed {len(questions)} questions from chunk!")
    except Exception as e:
        print("Error during LLM call or JSON parsing:", e)

if __name__ == "__main__":
    asyncio.run(test_pdf())
