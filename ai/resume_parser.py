import json
import os
import pdfplumber
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

RESUME_PDF = "data/resume.pdf"
PROFILE_JSON = "data/profile.json"

EXTRACT_PROMPT = """
You are a resume parser. Extract structured information from the resume text below and return ONLY a valid JSON object — no markdown, no explanation.

The JSON must follow this exact shape:
{
  "name": "Full Name",
  "email": "email@example.com",
  "phone": "+1 (555) 000-0000",
  "location": "City, State",
  "linkedin": "https://linkedin.com/in/...",
  "github": "https://github.com/...",
  "summary": "2-3 sentence professional summary",
  "skills": ["Skill1", "Skill2"],
  "experience": [
    {
      "title": "Job Title",
      "company": "Company Name",
      "location": "City, State or Remote",
      "start": "YYYY-MM",
      "end": "Present or YYYY-MM",
      "summary": "Brief description of responsibilities and impact"
    }
  ],
  "education": [
    {
      "degree": "Degree Name",
      "school": "School Name",
      "year": "YYYY"
    }
  ],
  "target_roles": ["software engineer", "frontend engineer", "backend engineer", "fullstack engineer"]
}

Rules:
- If a field is not found in the resume, use an empty string "" or empty array []
- For target_roles, infer from the candidate's experience and skills — do not leave empty
- Keep experience summaries concise (1-2 sentences max)

Resume text:
{resume_text}
""".strip()


def _extract_text(pdf_path: str) -> str:
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n\n".join(text_parts)


def parse_resume(pdf_path: str = RESUME_PDF) -> dict:
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(
            f"Resume not found at '{pdf_path}'. "
            "Please place your PDF resume at data/resume.pdf and try again."
        )

    print(f"Reading resume from {pdf_path}...")
    resume_text = _extract_text(pdf_path)

    if not resume_text.strip():
        raise ValueError("Could not extract text from the PDF. Make sure it is a text-based PDF, not a scanned image.")

    print("Extracting profile with AI...")
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": EXTRACT_PROMPT.replace("{resume_text}", resume_text),
            }
        ],
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()
    profile = json.loads(raw)
    return profile


def save_profile(profile: dict, output_path: str = PROFILE_JSON) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(profile, f, indent=2)
    print(f"Profile saved to {output_path}")


if __name__ == "__main__":
    profile = parse_resume()
    save_profile(profile)
    print(f"\nExtracted profile for: {profile.get('name', 'Unknown')}")
    print(f"Skills: {', '.join(profile.get('skills', []))}")
    print(f"Target roles: {', '.join(profile.get('target_roles', []))}")
