import os
import re
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT

OUTPUT_DIR = "output/coverletters"


def _safe_filename(company: str, job_id: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", company.lower()).strip()
    slug = re.sub(r"[\s]+", "_", slug)
    return f"{slug}_{job_id}.pdf"


def generate_pdf(cover_letter_text: str, job: dict, profile: dict) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    filename = _safe_filename(job.get("company", "company"), job.get("id", "0"))
    filepath = os.path.join(OUTPUT_DIR, filename)

    doc = SimpleDocTemplate(
        filepath,
        pagesize=LETTER,
        leftMargin=1 * inch,
        rightMargin=1 * inch,
        topMargin=1 * inch,
        bottomMargin=1 * inch,
    )

    styles = getSampleStyleSheet()

    name_style = ParagraphStyle(
        "Name",
        parent=styles["Normal"],
        fontSize=16,
        leading=20,
        fontName="Helvetica-Bold",
        alignment=TA_LEFT,
    )

    contact_style = ParagraphStyle(
        "Contact",
        parent=styles["Normal"],
        fontSize=9,
        leading=13,
        fontName="Helvetica",
        textColor=(0.4, 0.4, 0.4),
        alignment=TA_LEFT,
    )

    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=11,
        leading=16,
        fontName="Helvetica",
        alignment=TA_LEFT,
        spaceAfter=10,
    )

    contact_parts = [profile.get("email", ""), profile.get("phone", ""), profile.get("location", "")]
    contact_line = "  |  ".join(p for p in contact_parts if p)

    elements = [
        Paragraph(profile.get("name", ""), name_style),
        Spacer(1, 4),
        Paragraph(contact_line, contact_style),
        Spacer(1, 24),
    ]

    for paragraph in cover_letter_text.split("\n\n"):
        text = paragraph.strip()
        if text:
            elements.append(Paragraph(text, body_style))
            elements.append(Spacer(1, 6))

    doc.build(elements)
    return filepath


if __name__ == "__main__":
    import json

    with open("data/jobs.json") as f:
        jobs = json.load(f)

    with open("data/profile.json") as f:
        profile = json.load(f)

    from ai.coverletter import generate_cover_letter

    job = jobs[0]
    letter = generate_cover_letter(job, profile)
    path = generate_pdf(letter, job, profile)
    print(f"PDF saved to: {path}")
