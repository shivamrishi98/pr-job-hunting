import requests
import json
import time

API_URL = "https://remoteok.com/api"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://remoteok.com/",
    "Origin": "https://remoteok.com",
}

# Role keywords matched against job title and tags
ROLE_KEYWORDS = [
    "software engineer",
    "software developer",
    "sde",
    "frontend",
    "front-end",
    "front end",
    "backend",
    "back-end",
    "back end",
    "fullstack",
    "full-stack",
    "full stack",
]

# Location keywords matched against the location field
USA_KEYWORDS = [
    "usa",
    "us only",
    "united states",
    "u.s.",
    "remote - us",
    "remote us",
    # US states / major cities that appear in remoteok locations
    ", al", ", ak", ", az", ", ar", ", ca", ", co", ", ct", ", de", ", fl",
    ", ga", ", hi", ", id", ", il", ", in", ", ia", ", ks", ", ky", ", la",
    ", me", ", md", ", ma", ", mi", ", mn", ", ms", ", mo", ", mt", ", ne",
    ", nv", ", nh", ", nj", ", nm", ", ny", ", nc", ", nd", ", oh", ", ok",
    ", or", ", pa", ", ri", ", sc", ", sd", ", tn", ", tx", ", ut", ", vt",
    ", va", ", wa", ", wv", ", wi", ", wy",
]


def _is_software_role(position: str, tags: list[str]) -> bool:
    position_lower = position.lower()
    tags_lower = [t.lower() for t in tags]
    return (
        any(kw in position_lower for kw in ROLE_KEYWORDS)
        or any(kw in tag for kw in ROLE_KEYWORDS for tag in tags_lower)
    )


def _is_usa_job(location: str, tags: list[str]) -> bool:
    location_lower = location.lower()
    tags_lower = [t.lower() for t in tags]
    return (
        any(kw in location_lower for kw in USA_KEYWORDS)
        or "usa" in tags_lower
        or "us" in tags_lower
    )


def _parse_job(raw: dict) -> dict:
    return {
        "id": raw.get("id", ""),
        "title": raw.get("position", ""),
        "company": raw.get("company", ""),
        "location": raw.get("location", "") or "Remote",
        "tags": raw.get("tags", []),
        "salary_min": raw.get("salary_min", 0),
        "salary_max": raw.get("salary_max", 0),
        "date": raw.get("date", ""),
        "url": raw.get("url", ""),
        "apply_url": raw.get("apply_url", ""),
        "source": "remoteok",
    }


def fetch_remoteok_jobs() -> list[dict]:
    time.sleep(1)  # polite delay before hitting the API
    try:
        response = requests.get(API_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 403:
            print("[remoteok] 403 Forbidden — RemoteOK may block cloud IPs (e.g. Render). Skipping.")
            return []
        raise
    except Exception as e:
        print(f"[remoteok] Request failed: {e}")
        return []

    # First item is metadata (legal/last_updated), not a job listing
    raw_jobs = [item for item in data if isinstance(item, dict) and "id" in item]

    jobs = []
    for raw in raw_jobs:
        position = raw.get("position", "")
        tags = raw.get("tags", []) or []
        location = raw.get("location", "") or ""

        if not _is_software_role(position, tags):
            continue
        if not _is_usa_job(location, tags):
            continue

        jobs.append(_parse_job(raw))

    return jobs


if __name__ == "__main__":
    jobs = fetch_remoteok_jobs()

    with open("data/jobs.json", "w") as f:
        json.dump(jobs, f, indent=2)

    print(f"Saved {len(jobs)} jobs")