import json
import os
from dotenv import load_dotenv

load_dotenv()

from scraper.remoteok import fetch_remoteok_jobs
from ai.resume_parser import parse_resume, save_profile
from ai.filter import rank_jobs
from ai.coverletter import generate_cover_letter
from ai.pdf import generate_pdf

JOBS_FILE = "data/jobs.json"
PROFILE_FILE = "data/profile.json"
RESUME_FILE = "data/resume.pdf"
TOP_N = 5


def load_profile() -> dict:
    if not os.path.exists(PROFILE_FILE):
        if os.path.exists(RESUME_FILE):
            print("--- Profile not found. Parsing resume PDF ---")
            profile = parse_resume(RESUME_FILE)
            save_profile(profile, PROFILE_FILE)
        else:
            raise FileNotFoundError(
                "Neither data/profile.json nor data/resume.pdf found. "
                "Please add your resume PDF at data/resume.pdf and re-run."
            )

    with open(PROFILE_FILE) as f:
        return json.load(f)


def save_jobs(jobs: list[dict]) -> None:
    os.makedirs("data", exist_ok=True)
    with open(JOBS_FILE, "w") as f:
        json.dump(jobs, f, indent=2)


def run():
    profile = load_profile()

    print("--- Step 1: Scraping jobs ---")
    jobs = fetch_remoteok_jobs()
    save_jobs(jobs)
    print(f"  Found {len(jobs)} matching jobs")

    print(f"\n--- Step 2: Ranking top {TOP_N} jobs with AI ---")
    top_jobs = rank_jobs(jobs, profile, top_n=TOP_N)
    for i, job in enumerate(top_jobs, 1):
        print(f"  {i}. [{job['fit_score']}] {job['title']} @ {job['company']}")

    print("\n--- Step 3: Generating cover letters and PDFs ---")
    results = []
    for job in top_jobs:
        print(f"  Writing cover letter for {job['title']} @ {job['company']}...")
        try:
            letter = generate_cover_letter(job, profile)
            pdf_path = generate_pdf(letter, job, profile)
            results.append({**job, "pdf_path": pdf_path})
            print(f"    Saved: {pdf_path}")
        except Exception as e:
            print(f"    Failed: {e}")

    # Save ranked results (with pdf_path) for the UI to read
    ranked_file = "data/ranked_jobs.json"
    with open(ranked_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nDone. {len(results)} cover letter(s) ready.")
    print("Run  python app.py  to open the download UI.")


if __name__ == "__main__":
    run()
