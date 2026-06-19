"""Configuration loader — parses .env, application data, and job list."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
SCREENSHOTS_DIR = OUTPUT_DIR / "screenshots"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# LinkedIn credentials
LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")

# File paths
RESUME_PATH = os.getenv("RESUME_PATH", "")
COVER_LETTER_GENERIC_PATH = os.getenv("COVER_LETTER_GENERIC_PATH", "")
COVER_LETTERS_MD_PATH = os.getenv("COVER_LETTERS_MD_PATH", "")
JOBS_MD_PATH = os.getenv("JOBS_MD_PATH", "")

# Rate limiting
MAX_APPS_PER_HOUR = int(os.getenv("MAX_APPS_PER_HOUR", "5"))
MIN_DELAY_SECONDS = int(os.getenv("MIN_DELAY_SECONDS", "30"))
MAX_DELAY_SECONDS = int(os.getenv("MAX_DELAY_SECONDS", "120"))

# Browser
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

# Mode
MODE = os.getenv("MODE", "apply")  # "apply" or "scan"

# Storage state path
STORAGE_STATE_PATH = OUTPUT_DIR / "storageState.json"
APPLICATIONS_LOG_PATH = OUTPUT_DIR / "applications_log.csv"
SCANNED_QUESTIONS_PATH = OUTPUT_DIR / "scanned_questions.json"


def parse_jobs_from_md(md_path: str = None) -> List[Dict]:
    """Parse jobs_50.md into a list of job dicts."""
    path = md_path or JOBS_MD_PATH
    with open(path, "r") as f:
        content = f.read()

    jobs = []
    # Match table rows with job data: | # | Title | Company | Salary | Priority | URL |
    pattern = r"\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(https?://\S+)\s*\|"
    for match in re.finditer(pattern, content):
        job_id = int(match.group(1))
        title = match.group(2).strip()
        company = match.group(3).strip()
        salary = match.group(4).strip()
        priority = match.group(5).strip()
        url = match.group(6).strip()

        # Determine group based on job_id
        if job_id <= 17:
            group = "A"
        elif job_id <= 32:
            group = "B"
        elif job_id <= 43:
            group = "C"
        else:
            group = "D"

        # Determine salary level
        if job_id in [7, 19, 21]:
            salary_level = "senior"  # £90,000
        else:
            salary_level = "analyst"  # £64,000

        jobs.append({
            "id": job_id,
            "title": title,
            "company": company,
            "salary": salary,
            "priority": priority,
            "url": url,
            "group": group,
            "salary_level": salary_level,
        })

    return jobs


def get_application_answers() -> dict:
    """Return pre-built application answers dict."""
    answers_path = DATA_DIR / "application_answers.json"
    if answers_path.exists():
        with open(answers_path) as f:
            return json.load(f)
    return {}


def save_jobs_json(jobs: List[Dict]):
    """Save parsed jobs to JSON for reference."""
    with open(DATA_DIR / "jobs.json", "w") as f:
        json.dump(jobs, f, indent=2)
