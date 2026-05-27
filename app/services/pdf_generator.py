import io
from fpdf import FPDF


class NotesPDF(FPDF):
    def __init__(self, exam_name="", subject_name="", chapter_name="", topic_name="", subtopic_name=""):
        super().__init__()
        self.exam_name = exam_name
        self.subject_name = subject_name
        self.chapter_name = chapter_name
        self.topic_name = topic_name
        self.subtopic_name = subtopic_name

    def header(self):
        self.set_fill_color(79, 70, 229)
        self.rect(0, 0, 210, 14, 'F')
        self.set_text_color(255, 255, 255)
        self.set_font("Arial", "B", 8)
        header_text = f"{self.exam_name}  >  {self.subject_name}  >  {self.chapter_name}  >  {self.topic_name}"
        if len(header_text) > 110:
            header_text = header_text[:107] + "..."
        self.set_y(4)
        self.cell(0, 6, header_text, align="L")
        self.ln(12)

    def footer(self):
        self.set_y(-15)
        self.set_font("Arial", "I", 8)
        self.set_text_color(156, 163, 175)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def clean_for_pdf(text: str) -> str:
    """Replace emoji/unicode characters with safe ASCII equivalents for FPDF."""
    if not text:
        return ""

    replacements = {
        "\U0001f4cc": "Note: ",        # 📌
        "\U0001f4a1": "Key Concept: ", # 💡
        "\u26a1": "Quick Revision: ",  # ⚡
        "\U0001f4dd": "Quiz: ",        # 📝
        "\U0001f4da": "Notes: ",       # 📚
        "\U0001f3af": "Target: ",      # 🎯
        "\u2705": "Verified: ",        # ✅
        "\u274c": "Warning: ",         # ❌
        "\U0001f50d": "Details: ",     # 🔍
        "\U0001f4d6": "Overview: ",    # 📖
        "\U0001f4cb": "Summary: ",     # 📋
        "\U0001f4e5": "Download: ",    # 📥
        "\u2014": "-",                 # —
        "\u2013": "-",                 # –
        "\u201c": '"',                 # "
        "\u201d": '"',                 # "
        "\u2018": "'",                 # '
        "\u2019": "'",                 # '
        "\u2022": "*",                 # •
        "\u2026": "...",               # …
        "\xa0": " ",                   # non-breaking space
        "\u2192": "->",                # →
        "\u2190": "<-",                # ←
        "\u2248": "~=",                # ≈
        "\u2260": "!=",                # ≠
        "\u2264": "<=",                # ≤
        "\u2265": ">=",                # ≥
        "\u03b1": "alpha",             # α
        "\u03b2": "beta",              # β
        "\u03c0": "pi",               # π
        "\u03bc": "mu",               # μ
        "\u03c3": "sigma",            # σ
        "\u00b2": "^2",               # ²
        "\u00b3": "^3",               # ³
        "\u221a": "sqrt",             # √
        "\u222b": "integral",         # ∫
        "\u2211": "sum",              # ∑
        "\u2202": "d/d",              # ∂
    }
    for k, v in replacements.items():
        text = text.replace(k, v)

    # Final safe encode — replace anything not latin-1 with '?'
    return text.encode('latin-1', 'replace').decode('latin-1')


import re as _re


def _strip_html(text: str) -> str:
    """Remove HTML tags and unescape HTML entities."""
    import html
    text = html.unescape(text)
    text = _re.sub(r'<[^>]+>', '', text)
    return text.strip()


def _render_markdown_lines(pdf: FPDF, text: str):
    """Parse and render a markdown text block into the FPDF document."""
    if not text:
        return

    lines = text.split("\n")
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            pdf.ln(3)
            continue

        # Skip image lines
        if stripped.startswith("!["): 
            continue

        # Code block toggle
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            pdf.ln(2)
            continue

        # Skip/flatten HTML lines
        if stripped.startswith("<"):
            flat = _strip_html(stripped)
            if flat:
                clean = clean_for_pdf(flat)
                pdf.set_font("Arial", "I", 9)
                pdf.set_text_color(79, 70, 229)
                pdf.set_x(15)
                pdf.multi_cell(180, 6, clean.replace("**", ""))
            continue

        clean = clean_for_pdf(_strip_html(line))

        if not in_code_block:
            if stripped.startswith("# "):
                pdf.ln(4)
                pdf.set_font("Arial", "B", 13)
                pdf.set_text_color(79, 70, 229)
                pdf.multi_cell(0, 8, clean.lstrip("# ").strip())
                pdf.ln(2)
            elif stripped.startswith("## "):
                pdf.ln(3)
                pdf.set_font("Arial", "B", 11)
                pdf.set_text_color(17, 24, 39)
                pdf.multi_cell(0, 7, clean.lstrip("## ").strip())
                pdf.ln(2)
            elif stripped.startswith("### "):
                pdf.ln(2)
                pdf.set_font("Arial", "B", 10)
                pdf.set_text_color(31, 41, 55)
                pdf.multi_cell(0, 6, clean.lstrip("### ").strip())
                pdf.ln(1)
            elif stripped.startswith("#### ") or stripped.startswith("##### "):
                pdf.ln(2)
                pdf.set_font("Arial", "B", 9.5)
                pdf.set_text_color(55, 65, 81)
                pdf.multi_cell(0, 6, clean.lstrip("#").strip())
                pdf.ln(1)
            elif stripped.startswith("- ") or stripped.startswith("* "):
                pdf.set_font("Arial", "", 9.5)
                pdf.set_text_color(55, 65, 81)
                pdf.set_x(15)
                pdf.cell(4, 6, "-", ln=0)
                content = clean.strip()[2:].replace("**", "").strip()
                pdf.multi_cell(0, 6, content)
            elif stripped.startswith("> "):
                # Blockquote / callout
                pdf.set_font("Arial", "I", 9)
                pdf.set_text_color(100, 116, 139)
                pdf.set_x(18)
                pdf.multi_cell(170, 6, clean.lstrip("> ").replace("**", "").strip())
            else:
                pdf.set_font("Arial", "", 9.5)
                pdf.set_text_color(55, 65, 81)
                pdf.multi_cell(0, 6, clean.replace("**", ""))
        else:
            # Code block
            pdf.set_font("Courier", "", 8.5)
            pdf.set_text_color(185, 28, 28)
            pdf.set_fill_color(243, 244, 246)
            pdf.set_x(15)
            pdf.multi_cell(180, 5, clean_for_pdf(line), fill=1)


def generate_notes_pdf_bytes(
    subtopic_name: str,
    topic_name: str,
    chapter_name: str,
    subject_name: str,
    exam_name: str,
    description_text: str = "",
    notes_text: str = "",
) -> bytes:
    """
    Generate a PDF from description + notes content.
    Returns the PDF as raw bytes (no temp file, no Windows locking).
    """
    pdf = NotesPDF(
        exam_name=clean_for_pdf(exam_name),
        subject_name=clean_for_pdf(subject_name),
        chapter_name=clean_for_pdf(chapter_name),
        topic_name=clean_for_pdf(topic_name),
        subtopic_name=clean_for_pdf(subtopic_name),
    )
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # ── Title ──────────────────────────────────────────────
    pdf.set_y(22)
    pdf.set_text_color(31, 41, 55)
    pdf.set_font("Arial", "B", 18)
    pdf.multi_cell(0, 10, clean_for_pdf(subtopic_name))

    # Decorative underline
    pdf.set_draw_color(79, 70, 229)
    pdf.set_line_width(0.8)
    y_after_title = pdf.get_y() + 2
    pdf.line(10, y_after_title, 200, y_after_title)
    pdf.ln(8)

    # ── Description / Study Material section ──────────────
    if description_text and description_text.strip():
        # Section label
        pdf.set_font("Arial", "B", 10)
        pdf.set_fill_color(238, 242, 255)   # Light indigo
        pdf.set_text_color(79, 70, 229)
        pdf.set_draw_color(79, 70, 229)
        pdf.set_line_width(0.3)
        pdf.cell(0, 8, "  Overview / Study Material", border="LB", fill=1, ln=1)
        pdf.ln(4)

        _render_markdown_lines(pdf, description_text)
        pdf.ln(6)

    # ── Notes section ──────────────────────────────────────
    if notes_text and notes_text.strip():
        # Section label
        pdf.set_font("Arial", "B", 10)
        pdf.set_fill_color(255, 251, 235)   # Light amber
        pdf.set_text_color(180, 83, 9)
        pdf.set_draw_color(180, 83, 9)
        pdf.set_line_width(0.3)
        pdf.cell(0, 8, "  Detailed Revision Notes", border="LB", fill=1, ln=1)
        pdf.ln(4)

        _render_markdown_lines(pdf, notes_text)

    # ── Return raw bytes — no temp files, no Windows locking ──
    raw = pdf.output(dest='S')
    # In fpdf 1.7.2 (Python 3), output(dest='S') can return str or bytes
    if isinstance(raw, str):
        return raw.encode('latin-1')
    return bytes(raw)
