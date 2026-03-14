import json
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

COVER_LETTER_PROMPT = """
You are an expert career coach and professional writer. Write a concise, compelling cover letter for the following job.

Guidelines:
- Address the letter to the hiring team at the company
- Open with genuine enthusiasm for the specific role and company
- In 1-2 paragraphs, connect the candidate's experience and skills directly to the job requirements
- Close with a clear call to action
- Keep the tone professional but warm
- Total length: 3 short paragraphs, no longer
- Do NOT include a subject line, date, or postal addresses — just the letter body starting with "Dear Hiring Team,"

Candidate profile:
{profile}

Job:
{job}

Write the cover letter now.
""".strip()


def generate_cover_letter(job: dict, profile: dict) -> str:
    profile_text = json.dumps({
        "name": profile.get("name"),
        "summary": profile.get("summary"),
        "skills": profile.get("skills"),
        "experience": profile.get("experience"),
        "education": profile.get("education"),
    }, indent=2)

    job_text = json.dumps({
        "title": job.get("title"),
        "company": job.get("company"),
        "location": job.get("location"),
        "tags": job.get("tags"),
        "fit_reason": job.get("fit_reason", ""),
    }, indent=2)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": COVER_LETTER_PROMPT.format(profile=profile_text, job=job_text),
            }
        ],
        temperature=0.7,
    )

    return response.choices[0].message.content.strip()


if __name__ == "__main__":
    with open("data/jobs.json") as f:
        jobs = json.load(f)

    with open("data/profile.json") as f:
        profile = json.load(f)

    job = jobs[0]
    letter = generate_cover_letter(job, profile)
    print(f"Cover letter for {job['title']} @ {job['company']}:\n")
    print(letter)
