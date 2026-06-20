"""Configuration loader — reads .env, application data, and job list."""

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# LinkedIn credentials
LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")

# File paths
RESUME_PATH = Path(os.getenv("RESUME_PATH", ""))
COVER_LETTER_DIR = Path(os.getenv("COVER_LETTER_DIR", ""))
GENERIC_COVER_LETTER = Path(os.getenv("GENERIC_COVER_LETTER", ""))

# Rate limiting
MAX_APPS_PER_HOUR = int(os.getenv("MAX_APPS_PER_HOUR", "5"))
MIN_DELAY_SECONDS = int(os.getenv("MIN_DELAY_SECONDS", "30"))
MAX_DELAY_SECONDS = int(os.getenv("MAX_DELAY_SECONDS", "120"))

# Mode
MODE = os.getenv("MODE", "review")  # "auto" or "review"

# Output paths
OUTPUT_DIR = BASE_DIR / "output"
SCREENSHOTS_DIR = OUTPUT_DIR / "screenshots"
STORAGE_STATE = OUTPUT_DIR / "storageState.json"
LOG_FILE = OUTPUT_DIR / "applications_log.csv"

# Data paths
DATA_DIR = BASE_DIR / "data"
JOBS_JSON = DATA_DIR / "jobs.json"
ANSWERS_JSON = DATA_DIR / "application_answers.json"


def load_jobs() -> list[dict]:
    """Load jobs from data/jobs.json."""
    with open(JOBS_JSON) as f:
        return json.load(f)


def load_answers() -> dict:
    """Load application answers from data/application_answers.json."""
    with open(ANSWERS_JSON) as f:
        return json.load(f)


def parse_jobs_from_markdown(md_path: str) -> list[dict]:
    """Parse jobs_50.md into structured job list. Used once to generate jobs.json."""
    jobs = []
    current_group = ""

    with open(md_path) as f:
        content = f.read()

    # Find group headings
    group_pattern = re.compile(r"^## (Group \w+:.*)", re.MULTILINE)
    # Find table rows with job data
    row_pattern = re.compile(
        r"\|\s*(\d+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(https?://\S+)\s*\|"
    )

    lines = content.split("\n")
    for line in lines:
        group_match = group_pattern.match(line)
        if group_match:
            current_group = group_match.group(1).strip()
            continue

        row_match = row_pattern.match(line)
        if row_match:
            job_id = int(row_match.group(1))
            title = row_match.group(2).strip()
            company = row_match.group(3).strip()
            salary = row_match.group(4).strip()
            priority = row_match.group(5).strip()
            url = row_match.group(6).strip()

            jobs.append(
                {
                    "id": job_id,
                    "title": title,
                    "company": company,
                    "salary": salary,
                    "priority": priority,
                    "url": url,
                    "group": current_group,
                }
            )

    return jobs


if __name__ == "__main__":
    # Generate jobs.json from jobs_50.md
    jobs_md = COVER_LETTER_DIR / "jobs_50.md"
    if jobs_md.exists():
        jobs = parse_jobs_from_markdown(str(jobs_md))
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(JOBS_JSON, "w") as f:
            json.dump(jobs, f, indent=2)
        print(f"Generated {len(jobs)} jobs -> {JOBS_JSON}")
    else:
        print(f"jobs_50.md not found at {jobs_md}")
