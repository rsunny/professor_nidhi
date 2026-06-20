"""Post-processing filter — apply salary and relevance filters to scraped jobs."""

import json
import re
from pathlib import Path

from config import DATA_DIR

MIN_SALARY = 60000  # Minimum acceptable salary (£60k)
PREFERRED_SALARY = 65000  # Preferred minimum (£65k)


def extract_salary(job: dict) -> tuple[int | None, int | None]:
    """Extract min/max salary from job data. Returns (min, max) or (None, None)."""
    salary_text = job.get("salary", "").lower().replace(",", "").replace("£", "")
    title = job.get("title", "").lower()
    
    if not salary_text or salary_text == "competitive":
        return None, None
    
    # Find numbers that look like salaries (5-6 digit numbers)
    numbers = re.findall(r"(\d{5,6})", salary_text)
    
    if len(numbers) >= 2:
        return int(numbers[0]), int(numbers[1])
    elif len(numbers) == 1:
        return int(numbers[0]), int(numbers[0])
    
    # Try "£XYk" format
    k_numbers = re.findall(r"(\d+)k", salary_text)
    if k_numbers:
        nums = [int(n) * 1000 for n in k_numbers]
        if len(nums) >= 2:
            return nums[0], nums[1]
        return nums[0], nums[0]
    
    return None, None


def filter_by_salary(jobs: list[dict]) -> list[dict]:
    """Filter jobs by salary. Keep if salary >= £60k OR salary unknown (most finance roles)."""
    filtered = []
    removed = 0
    
    for job in jobs:
        min_sal, max_sal = extract_salary(job)
        
        if min_sal is None:
            # Unknown salary — keep (most investment bank/HF roles don't list salary)
            filtered.append(job)
        elif max_sal and max_sal >= MIN_SALARY:
            # Max salary meets minimum
            job["salary_range"] = f"£{min_sal:,}-£{max_sal:,}"
            if max_sal >= PREFERRED_SALARY:
                job["relevance_score"] = job.get("relevance_score", 0) + 5  # Bonus
            filtered.append(job)
        elif min_sal >= MIN_SALARY:
            job["salary_range"] = f"£{min_sal:,}+"
            filtered.append(job)
        else:
            removed += 1
    
    return filtered, removed


def main():
    """Apply salary filter to scraped jobs."""
    jobs_path = DATA_DIR / "jobs_new_300.json"
    
    if not jobs_path.exists():
        print("No jobs_new_300.json found. Run job_scraper.py first.")
        return
    
    with open(jobs_path) as f:
        jobs = json.load(f)
    
    print(f"Loaded {len(jobs)} jobs")
    
    # Apply salary filter
    filtered, removed = filter_by_salary(jobs)
    print(f"After salary filter (>= £{MIN_SALARY:,}): {len(filtered)} kept, {removed} removed")
    
    # Re-sort by relevance
    filtered.sort(key=lambda j: j.get("relevance_score", 0), reverse=True)
    
    # Save filtered results
    output_path = DATA_DIR / "jobs_filtered.json"
    with open(output_path, "w") as f:
        json.dump(filtered, f, indent=2)
    print(f"Saved: {output_path}")
    
    # Print stats
    with_salary = [j for j in filtered if "salary_range" in j]
    without_salary = [j for j in filtered if "salary_range" not in j]
    print(f"\n  With salary listed: {len(with_salary)}")
    print(f"  Competitive/unlisted: {len(without_salary)}")
    
    if with_salary:
        print(f"\n  Salary examples:")
        for j in with_salary[:10]:
            print(f"    {j['salary_range']:20s} | {j['title'][:50]} | {j.get('company', '?')[:25]}")


if __name__ == "__main__":
    main()
