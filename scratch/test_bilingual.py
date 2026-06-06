import fitz
import re

def parse_mcqs_bilingual(text):
    # Split on question numbers
    pattern = r'\n\s*(\d{1,3})\.\s*(?=\n|[A-Z]|\?)'
    parts = re.split(pattern, text)
    
    questions = []
    i = 1
    while i < len(parts) - 1:
        q_num = parts[i]
        q_body = parts[i+1].strip()
        i += 2
        
        # Check if there is Hindi text (Devanagari range: \u0900-\u097F)
        hindi_match = re.search(r'[\u0900-\u097f]', q_body)
        
        english_part = q_body
        hindi_part = ""
        
        if hindi_match:
            split_idx = hindi_match.start()
            # We want to backtrack to the beginning of the line/paragraph where Hindi starts
            # usually there is a newline or double newline before the Hindi question starts
            before_hindi = q_body[:split_idx]
            after_hindi = q_body[split_idx:]
            
            # Find the last newline in before_hindi to separate cleanly
            last_nl = before_hindi.rfind('\n')
            if last_nl != -1:
                english_part = before_hindi[:last_nl].strip()
                hindi_part = (before_hindi[last_nl:] + after_hindi).strip()
            else:
                english_part = before_hindi.strip()
                hindi_part = after_hindi.strip()
        
        # Helper to extract options from a part
        def extract_opts(part_text):
            opt_pattern = r'\(([A-E])\)\s*(.*?)(?=\s*\([A-E]\)|\Z)'
            matches = re.findall(opt_pattern, part_text, re.DOTALL)
            
            opts_dict = {}
            for letter, o_text in matches:
                o_clean = re.sub(r'\s+', ' ', o_text).strip()
                if o_clean:
                    opts_dict[letter.upper()] = o_clean
            
            # Get question text by stripping options
            first_opt = re.search(r'\([A-E]\)', part_text)
            q_text = part_text
            if first_opt:
                q_text = part_text[:first_opt.start()].strip()
            q_text = re.sub(r'\s+', ' ', q_text).strip()
            
            return q_text, opts_dict

        eng_q, eng_opts = extract_opts(english_part)
        hin_q, hin_opts = extract_opts(hindi_part) if hindi_part else ("", {})
        
        # Combine them beautifully
        combined_q = eng_q
        if hin_q:
            combined_q += " / " + hin_q
            
        combined_opts = []
        # Support letters A through E
        for letter in ['A', 'B', 'C', 'D', 'E']:
            opts_for_letter = []
            if letter in eng_opts:
                opts_for_letter.append(eng_opts[letter])
            if letter in hin_opts:
                opts_for_letter.append(hin_opts[letter])
                
            if opts_for_letter:
                combined_opts.append(" / ".join(opts_for_letter))
                
        if len(combined_opts) >= 2:
            questions.append({
                "question": combined_q,
                "options": combined_opts,
                "correct": ""
            })
            
    return questions

def test():
    pdf_path = "70th BPSC.pdf"
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += "\n" + page.get_text()
        
    questions = parse_mcqs_bilingual(text)
    print(f"Bilingual parser successfully extracted {len(questions)} questions!")
    for idx, q in enumerate(questions[:5]):
        q_safe = q["question"].encode('ascii', 'replace').decode('ascii')
        opts_safe = [o.encode('ascii', 'replace').decode('ascii') for o in q["options"]]
        print(f"\n--- Question {idx+1} ---")
        print("Q Text:", q_safe[:200])
        print("Options:", opts_safe)

if __name__ == "__main__":
    test()
