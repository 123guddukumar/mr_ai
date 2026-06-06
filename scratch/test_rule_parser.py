import fitz
import re
import json
import sys

def parse_mcqs_rule_based(text):
    # Split on: newline, optional spaces, 1-3 digits, a dot, followed by spaces or newline
    # Using lookahead or split pattern
    pattern = r'\n\s*(\d{1,3})\.\s*(?=\n|[A-Z]|\?)'
    parts = re.split(pattern, text)
    
    questions = []
    i = 1
    while i < len(parts) - 1:
        q_num = parts[i]
        q_body = parts[i+1].strip()
        i += 2
        
        # Extract options (A), (B), (C), (D)
        # BPSC questions sometimes have (E) too, so let's match (A)-(E)
        opt_pattern = r'\(([A-E])\)\s*(.*?)(?=\s*\([A-E]\)|\Z|\n\s*\d{1,3}\.)'
        opts_found = re.findall(opt_pattern, q_body, re.DOTALL)
        
        options = []
        clean_body = q_body
        
        if opts_found:
            opts_found = sorted(opts_found, key=lambda x: x[0])
            options = [opt_text.strip().replace('\n', ' ') for opt_letter, opt_text in opts_found]
            
            # The question text is everything before the first option marker like (A) or (a)
            first_opt_idx = re.search(r'\([A-E]\)', q_body)
            if first_opt_idx:
                clean_body = q_body[:first_opt_idx.start()].strip()
        
        clean_body = re.sub(r'\s+', ' ', clean_body).strip()
        
        # Deduplicate option text and clean it
        cleaned_options = []
        for o in options:
            o_clean = re.sub(r'\s+', ' ', o).strip()
            if o_clean:
                cleaned_options.append(o_clean)
                
        if len(cleaned_options) >= 2:
            questions.append({
                "question": clean_body,
                "options": cleaned_options,
                "correct": ""
            })
            
    return questions

def test():
    pdf_path = "70th BPSC.pdf"
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += "\n" + page.get_text()
        
    print(f"Total extracted text length: {len(text)}")
    questions = parse_mcqs_rule_based(text)
    print(f"Successfully extracted {len(questions)} questions using rule-based parser!")
    
    # Print first 5 questions safely
    for idx, q in enumerate(questions[:10]):
        q_safe = q["question"].encode('ascii', 'replace').decode('ascii')
        opts_safe = [o.encode('ascii', 'replace').decode('ascii') for o in q["options"]]
        print(f"\n--- Question {idx+1} ---")
        print("Q Text:", q_safe[:150])
        print("Options:", opts_safe)

if __name__ == "__main__":
    test()
