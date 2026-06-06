import fitz
import re

pdf_path = "70th BPSC.pdf"
doc = fitz.open(pdf_path)

# Extract first 3 pages
text = ""
for i in range(3):
    text += doc.load_page(i).get_text() + "\n"

print("--- RAW TEXT FROM FIRST 3 PAGES ---")
# Replace non-ascii for safe printing on Windows terminal
print(text.encode('ascii', 'replace').decode('ascii')[:3000])
