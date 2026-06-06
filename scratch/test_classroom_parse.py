import sys
import os

sys.path.insert(0, os.getcwd())

from app.routes.classroom import extract_text_from_pdf, parse_mcqs_rule_based

def main():
    pdf_path = "70th BPSC.pdf"
    if not os.path.exists(pdf_path):
        print(f"Error: {pdf_path} not found.")
        return

    with open(pdf_path, "rb") as f:
        contents = f.read()

    print("Extracting text...")
    raw_text = extract_text_from_pdf(contents, max_pages=35)
    print(f"Text length: {len(raw_text)}")

    print("Parsing MCQs using parse_mcqs_rule_based...")
    questions = parse_mcqs_rule_based(raw_text)
    print(f"Total questions parsed: {len(questions)}")

if __name__ == "__main__":
    main()
