import json
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SCORE_PROMPT = """
You are a job-fit analyst. Given a candidate profile and a job listing, return a JSON object with:
- "score": integer from 0 to 100 representing how well the candidate fits the role
- "reason": one sentence explaining the score

Respond ONLY with valid JSON. No markdown, no extra text.

Candidate profile:
{profile}

Job listing:
{job}
""".strip()


def _score_job(job: dict, profile: dict) -> dict:
    profile_text = json.dumps(profile, indent=2)
    job_text = json.dumps({
        "title": job.get("title"),
        "company": job.get("company"),
        "location": job.get("location"),
        "tags": job.get("tags"),
    }, indent=2)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": SCORE_PROMPT.format(profile=profile_text, job=job_text),
            }
        ],
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()
    result = json.loads(raw)
    return result


def rank_jobs(jobs: list[dict], profile: dict, top_n: int = 5) -> list[dict]:
    scored = []

    for job in jobs:
        try:
            result = _score_job(job, profile)
            scored.append({
                **job,
                "fit_score": result.get("score", 0),
                "fit_reason": result.get("reason", ""),
            })
        except Exception as e:
            print(f"[filter] Failed to score job {job.get('id')}: {e}")
            scored.append({**job, "fit_score": 0, "fit_reason": "scoring failed"})

    scored.sort(key=lambda j: j["fit_score"], reverse=True)
    return scored[:top_n]


if __name__ == "__main__":
    with open("data/jobs.json") as f:
        jobs = json.load(f)

    with open("data/profile.json") as f:
        profile = json.load(f)

    top_jobs = rank_jobs(jobs, profile, top_n=5)

    for job in top_jobs:
        print(f"[{job['fit_score']}] {job['title']} @ {job['company']} — {job['fit_reason']}")
