from fpdf import FPDF

class PDF(FPDF):
    def header(self):
        self.set_font("Arial", "B", 16)
        self.cell(0, 10, "Working Day Timeline (March 1 - March 13)", ln=True, align="C")
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font("Arial", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

pdf = PDF()
pdf.add_page()
pdf.set_font("Arial", size=12)

pdf.set_font("Arial", "B", 14)
pdf.cell(0, 10, "Summary of Work Done", ln=True)
pdf.set_font("Arial", size=12)
pdf.multi_cell(0, 10, "The following timeline summarizes the work done within the Antigravity assistant from March 1 to March 13, 2026, extracted from the saved chat histories.")
pdf.ln(5)

# Entry 1
pdf.set_font("Arial", "B", 12)
pdf.cell(0, 10, "March 6, 2026", ln=True)
pdf.set_font("Arial", "", 12)
pdf.multi_cell(0, 8, "- Planning Smart Bettiah Platform: Created a comprehensive project plan for a production-ready local discovery web platform called 'Smart Bettiah' for Bettiah city. Included a category system, smart search engine, map integration, and platform architecture.")
pdf.ln(2)
pdf.multi_cell(0, 8, "- Multilingual Bot Personality: Initialized work on enhancing the bot's conversational abilities. Developed custom keyword responses for greetings and inquiries in the user's preferred language (including Hindi/Hinglish).")
pdf.ln(5)

# Entry 2
pdf.set_font("Arial", "B", 12)
pdf.cell(0, 10, "March 7, 2026", ln=True)
pdf.set_font("Arial", "", 12)
pdf.multi_cell(0, 8, "- Multilingual Bot Personality (Continued): Continued enhancing bot conversation handling. Ensured proper translation of outputs, implemented session termination sequences, and handled inactivity timeouts gracefully.")
pdf.ln(5)

# Conclude
pdf.set_font("Arial", "I", 11)
pdf.multi_cell(0, 10, "Note: Sessions between March 1 and March 5, and March 8 to March 13 revealed no chat history records in the current Antigravity environment.")


# Save to output file
out_path = "C:/Users/LENOVO/.gemini/antigravity/brain/131b02bf-252c-438f-9716-6a88ec2918b4/timeline.pdf"
user_path = "C:/Users/LENOVO/Downloads/mr_ai_rag_v2/timeline.pdf"
pdf.output(out_path)
pdf.output(user_path)
