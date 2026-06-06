import sys
import os

pdf_path = "70th BPSC.pdf"
if not os.path.exists(pdf_path):
    print("Error: 70th BPSC.pdf not found in", os.getcwd())
    sys.exit(1)

print("Attempting to import fitz (PyMuPDF)...")
try:
    import fitz
    print("fitz imported successfully.")
    
    doc = fitz.open(pdf_path)
    print(f"Total pages: {len(doc)}")
    
    pages_text = []
    for i in range(min(5, len(doc))):
        page = doc.load_page(i)
        text = page.get_text()
        print(f"Page {i+1} character count: {len(text)}")
        pages_text.append(text)
        
    full_text = "\n".join(pages_text).strip()
    print(f"Sample of first 300 characters extracted:\n{full_text[:300]}")
    
except Exception as e:
    print("Failed to use fitz:", e)

print("\nAttempting to import pdfplumber...")
try:
    import pdfplumber
    print("pdfplumber imported successfully.")
    with pdfplumber.open(pdf_path) as pdf:
        print(f"Total pages via pdfplumber: {len(pdf.pages)}")
        for i in range(min(3, len(pdf.pages))):
            text = pdf.pages[i].extract_text()
            print(f"Page {i+1} via pdfplumber character count: {len(text) if text else 0}")
except Exception as e:
    print("Failed to use pdfplumber:", e)
