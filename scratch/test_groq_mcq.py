import sys
import os
import json
import re
from openai import AsyncOpenAI
import asyncio

async def test_pdf_groq():
    pdf_path = "70th BPSC.pdf"
    if not os.path.exists(pdf_path):
        print(f"Error: {pdf_path} not found.")
        return

    print("Attempting to import fitz (PyMuPDF)...")
    import fitz
    doc = fitz.open(pdf_path)
    print(f"Total pages: {len(doc)}")

    # Extract text from first 5 pages to make a nice chunk
    pages = []
    for i in range(min(5, len(doc))):
        pages.append(doc.load_page(i).get_text())
    text = "\n".join(pages).strip()
    print(f"Extracted {len(text)} characters from first 5 pages.")

    if not text:
        print("No text extracted!")
        return

    # Let's get the Groq API key from environment
    groq_key = os.environ.get("GROQ_API_KEY", "")
    
    print("Initiating AsyncOpenAI client for Groq...")
    client = AsyncOpenAI(api_key=groq_key, base_url="https://api.groq.com/openai/v1")
    
    chunk_text = text[:10000]
    
    prompt = f"""Extract all MCQ (Multiple Choice Questions) from the following exam text chunk.
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

    print("Sending request to Groq...")
    try:
        resp = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are an MCQ extraction expert. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2048,
            temperature=0.1
        )
        raw_response = resp.choices[0].message.content.strip()
        print("\n--- Raw Groq Response ---")
        print(raw_response.encode('ascii', 'replace').decode('ascii'))
        print("-------------------------\n")
        
        # Clean response
        clean = re.sub(r'```json\s*', '', raw_response)
        clean = re.sub(r'\s*```', '', clean).strip()
        first_bracket = clean.find('[')
        last_bracket = clean.rfind(']')
        if first_bracket != -1 and last_bracket != -1:
            clean = clean[first_bracket:last_bracket+1]
            
        questions = json.loads(clean)
        print(f"Successfully parsed {len(questions)} questions!")
        for idx, q in enumerate(questions[:2]):
            print(f"Q{idx+1}: {q.get('question', '')[:100]}")
            print(f"Options: {q.get('options', [])}")
            print(f"Correct: {q.get('correct', '')}")
            
    except Exception as e:
        print("Error during Groq call or parsing:", e)

if __name__ == "__main__":
    asyncio.run(test_pdf_groq())
