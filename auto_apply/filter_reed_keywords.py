"""Reed Keyword Filter — Fetch descriptions + fast keyword-based filtering.

Step 1: Fetch descriptions for all jobs with apply buttons (HTTP, parallel)
Step 2: Keyword filter (instant, no AI calls):
  - Exclude: contracts, day rates, temp, wrong field (IT/marketing/legal/construction)
  - Exclude: too senior (VP, Director, 10+ years)
  - Exclude: too technical (developer, engineer, python-heavy)
  - Include: finance, operations, trade, settlement, middle office, analyst

Saves descriptions + filtered list to disk.

Usage:
    python3 -u filter_reed_keywords.py
"""

import asyncio
import json
import os
import random
import re
import sys
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))

from config import DATA_DIR

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REED_FETCHED_FILE = DATA_DIR / "reed_jobs_fetched.json"
REED_DESCRIPTIONS_FILE = DATA_DIR / "reed_jobs_with_descriptions.json"
REED_READY_FILE = DATA_DIR / "reed_jobs_ready_to_apply.json"
REED_SESSION_FILE = DATA_DIR / "reed_storage_state.json"

MAX_CONCURRENT = 5
BATCH_SIZE = 25

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

# Exclude if title or description contains these (contract/temp indicators)
CONTRACT_PATTERNS = [
    r"per day", r"day rate", r"per diem", r"/day",
    r"outside ir35", r"inside ir35", r"ir35",
    r"\bcontract\b.*\bmonth", r"\bcontract\b.*\bweek",
    r"\b\d+[- ]month contract", r"\b\d+[- ]week contract",
    r"\bftc\b", r"fixed.?term", r"\btemp\b", r"temporary",
    r"interim", r"freelance",
]

# Exclude if title contains these (too senior)
SENIORITY_EXCLUDE = [
    r"\bvp\b", r"vice president", r"\bdirector\b", r"\bhead of\b",
    r"\bchief\b", r"\bcto\b", r"\bcfo\b", r"\bpartner\b",
    r"senior manager", r"managing director", r"\bmd\b",
    r"10\+? years", r"15\+? years", r"principal",
]

# Exclude if title/description indicates wrong field
WRONG_FIELD_PATTERNS = [
    r"\bdeveloper\b", r"\bengineer\b", r"\bdevops\b", r"\bsre\b",
    r"full.?stack", r"\bfront.?end\b", r"\bback.?end\b",
    r"\bmarketing\b", r"\bseo\b", r"\bppc\b", r"\bcontent\b",
    r"\blegal\b.*\bsecretary\b", r"\bsolicitor\b", r"\bparalegal\b",
    r"\bconstruction\b", r"\barchitect\b(?!.*solution)", r"\bbuilding\b",
    r"\bnursing\b", r"\bclinical\b", r"\bhealthcare\b(?!.*finance)",
    r"\bteacher\b", r"\bteaching\b",
    r"\brecruitment consultant\b",
    r"\bcyber\b", r"\binfosec\b", r"\bpenetration\b",
    r"\bhr\b.*\bsystem", r"\bhr\b.*\badvisor", r"\bpayroll\b",
    r"\bservice.?desk\b", r"\bhelpdesk\b",
    r"\bpharmac", r"\bgxp\b",
]

# Exclude if the role is too Python/technical heavy (description focus)
TECHNICAL_HEAVY_PATTERNS = [
    r"python.*essential", r"python.*required", r"strong python",
    r"java\b.*\brequired", r"c\+\+.*required", r"c#.*required",
    r"\bkubernetes\b", r"\bdocker\b.*required",
    r"machine learning.*required", r"data science.*essential",
    r"software development.*required",
]

# MUST match at least one (title or description) — finance/ops relevance
RELEVANCE_PATTERNS = [
    r"\btrade\b", r"\btrading\b", r"\bsettlement\b", r"\breconciliation\b",
    r"\bmiddle office\b", r"\boperations\b.*(?:analyst|associate|manager)",
    r"\bfinance\b.*\banalyst\b", r"\bfinancial\b.*\banalyst\b",
    r"\brisk\b.*\banalyst\b", r"\bprime broker",
    r"\bpost.?trade\b", r"\bclearing\b", r"\bcustody\b",
    r"\bfund account", r"\binvestment\b.*\boperation",
    r"\basset servic", r"\bcorporate action",
    r"\btreasury\b", r"\bpayments?\b.*(?:analyst|operation)",
    r"\bderivatives?\b", r"\bfixed income\b",
    r"\bloan\b.*(?:analyst|operation|servic)", r"\bcredit\b.*\boperation",
    r"\bportfolio\b.*\banalyst", r"\bfp&a\b",
    r"\bfinancial\b.*\boperation", r"\bfinancial\b.*\breport",
    r"\bregulatory\b.*\breport", r"\bcompliance\b.*\banalyst",
    r"\binvestment\b.*\banalyst", r"\bwealth\b.*\boperation",
    r"\bclient\b.*\boperation", r"\bclient\b.*\bservice.*(?:analyst|associate)",
    r"\bbusiness\b.*\banalyst\b.*(?:financ|bank|trade|operation)",
]

# London check
LONDON_PATTERNS = [
    r"\blondon\b", r"\bcity of london\b", r"\bcanary wharf\b",
    r"\bec[1-4]\b", r"\bwc[12]\b", r"\be14\b", r"\bsw1\b",
    r"\bremote\b", r"\bhybrid\b",
]

# Sponsorship signals (bonus — prioritize these)
SPONSORSHIP_POSITIVE = [
    r"sponsor", r"visa\b", r"skilled worker",
    r"tier 2", r"right to work.*assist",
]

# Anti-sponsorship (exclude if explicitly says no sponsorship)
SPONSORSHIP_NEGATIVE = [
    r"no sponsor", r"cannot sponsor", r"unable to sponsor",
    r"will not sponsor", r"won't sponsor", r"not able to sponsor",
    r"must have.*right to work", r"must already have.*right to work",
    r"only candidates with.*right to work",
]


def passes_filters(job: dict) -> tuple[bool, str, bool]:
    """Check if job passes all keyword filters.
    Returns (passes, reason, sponsors_visa)."""
    title = (job.get("title") or "").lower()
    desc = (job.get("description") or "").lower()
    combined = title + " " + desc

    # 0. Explicitly says no sponsorship — skip
    for pattern in SPONSORSHIP_NEGATIVE:
        if re.search(pattern, combined, re.I):
            return False, f"No sponsorship: matched '{pattern}'", False

    # 1. Contract/temp check
    for pattern in CONTRACT_PATTERNS:
        if re.search(pattern, combined, re.I):
            return False, f"Contract/temp: matched '{pattern}'", False

    # 2. Must be full-time (not part-time)
    if re.search(r"\bpart.?time\b", combined, re.I) and not re.search(r"\bfull.?time\b", combined, re.I):
        return False, "Part-time role", False

    # 3. Seniority check (title only)
    for pattern in SENIORITY_EXCLUDE:
        if re.search(pattern, title, re.I):
            return False, f"Too senior: matched '{pattern}'", False

    # 4. Wrong field check
    for pattern in WRONG_FIELD_PATTERNS:
        if re.search(pattern, title, re.I):
            return False, f"Wrong field: matched '{pattern}'", False

    # 5. Too technical check (description)
    for pattern in TECHNICAL_HEAVY_PATTERNS:
        if re.search(pattern, combined, re.I):
            return False, f"Too technical: matched '{pattern}'", False

    # 6. Relevance check — must match at least one finance/ops pattern
    relevant = False
    for pattern in RELEVANCE_PATTERNS:
        if re.search(pattern, combined, re.I):
            relevant = True
            break
    if not relevant:
        return False, "Not finance/operations related", False

    # 7. Location check
    location_ok = False
    for pattern in LONDON_PATTERNS:
        if re.search(pattern, combined, re.I):
            location_ok = True
            break
    if not location_ok:
        return False, "Not London/remote", False

    # 8. Check sponsorship signal (bonus, not a filter)
    sponsors = False
    for pattern in SPONSORSHIP_POSITIVE:
        if re.search(pattern, combined, re.I):
            sponsors = True
            break

    return True, "Passes all filters", sponsors


# ---------------------------------------------------------------------------
# HTTP fetch descriptions
# ---------------------------------------------------------------------------

def get_reed_cookies() -> dict:
    if not REED_SESSION_FILE.exists():
        return {}
    state = json.loads(REED_SESSION_FILE.read_text())
    cookies = {}
    for cookie in state.get("cookies", []):
        if "reed.co.uk" in cookie.get("domain", ""):
            cookies[cookie["name"]] = cookie["value"]
    return cookies


def extract_description(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    desc_el = soup.find("span", {"itemprop": "description"}) or soup.find("div", class_=re.compile(r"description|job-details|job_description"))
    if desc_el:
        return desc_el.get_text(separator=" ", strip=True)[:3000]
    main = soup.find("main") or soup.find("div", {"id": "main"})
    if main:
        return main.get_text(separator=" ", strip=True)[:3000]
    return ""


async def fetch_description(session: aiohttp.ClientSession, url: str, semaphore: asyncio.Semaphore) -> tuple[str, str]:
    async with semaphore:
        await asyncio.sleep(random.uniform(0.3, 0.8))
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    return url, extract_description(html)
                return url, ""
        except Exception:
            return url, ""


async def fetch_all_descriptions(jobs: list[dict]) -> list[dict]:
    """Fetch descriptions and add to job dicts. Returns updated jobs."""
    cookies = get_reed_cookies()
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    url_to_job = {j["url"]: j for j in jobs}

    async with aiohttp.ClientSession(cookies=cookies, headers=headers) as session:
        total = len(jobs)
        for batch_start in range(0, total, BATCH_SIZE):
            batch = jobs[batch_start:batch_start + BATCH_SIZE]
            tasks = [fetch_description(session, j["url"], semaphore) for j in batch]
            batch_results = await asyncio.gather(*tasks)

            for url, desc in batch_results:
                if url in url_to_job:
                    url_to_job[url]["description"] = desc

            processed = min(batch_start + BATCH_SIZE, total)
            with_desc = sum(1 for j in jobs[:processed] if j.get("description"))
            print(f"  Fetched: {processed}/{total} | With description: {with_desc}", flush=True)

            # Rate limit handling
            empty_count = sum(1 for _, d in batch_results if not d)
            if empty_count == len(batch):
                print(f"  WARNING: All empty. Waiting 15s...", flush=True)
                await asyncio.sleep(15)
            else:
                await asyncio.sleep(random.uniform(1.5, 3))

    return jobs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("  REED KEYWORD FILTER (Fast — No AI Calls)")
    print("=" * 60, flush=True)

    # Load fetched data
    fetched = json.loads(REED_FETCHED_FILE.read_text())
    has_apply = [j for j in fetched if j.get("has_apply_button")]
    print(f"  Jobs with apply button: {len(has_apply)}")

    # Step 1: Fetch descriptions
    print(f"\n  STEP 1: Fetching descriptions...\n", flush=True)
    jobs_with_desc = await fetch_all_descriptions(has_apply)

    # Save descriptions to disk
    REED_DESCRIPTIONS_FILE.write_text(json.dumps(jobs_with_desc, indent=2))
    print(f"\n  Saved descriptions to: {REED_DESCRIPTIONS_FILE}")

    # Step 2: Keyword filter
    print(f"\n  STEP 2: Keyword filtering...\n", flush=True)

    relevant = []
    sponsorship_jobs = []
    skip_reasons = {}

    for job in jobs_with_desc:
        if not job.get("description"):
            continue
        passes, reason, sponsors = passes_filters(job)
        if passes:
            job["sponsors_visa"] = sponsors
            relevant.append(job)
            if sponsors:
                sponsorship_jobs.append(job)
        else:
            category = reason.split(":")[0] if ":" in reason else reason
            skip_reasons[category] = skip_reasons.get(category, 0) + 1

    # Print skip reasons
    print(f"  Filter results:")
    print(f"    Total with descriptions: {sum(1 for j in jobs_with_desc if j.get('description'))}")
    print(f"    PASSED (relevant): {len(relevant)}")
    print(f"    Of which sponsor visa: {len(sponsorship_jobs)}")
    print(f"    Skipped breakdown:")
    for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
        print(f"      {reason}: {count}")

    # Sort: sponsorship jobs first, then rest
    relevant.sort(key=lambda j: (not j.get("sponsors_visa", False)))

    # Save relevant jobs
    ready = []
    for j in relevant:
        ready.append({
            "url": j["url"],
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "description": j.get("description", "")[:500],
            "sponsors_visa": j.get("sponsors_visa", False),
        })

    REED_READY_FILE.write_text(json.dumps(ready, indent=2))

    print(f"\n  Saved {len(ready)} relevant jobs to: {REED_READY_FILE}")
    print(f"\n  SPONSORSHIP JOBS (priority):")
    for j in ready:
        if j["sponsors_visa"]:
            print(f"    * {j['title'][:60]}")

    print(f"\n  OTHER RELEVANT JOBS:")
    for j in ready[:30]:
        if not j["sponsors_visa"]:
            print(f"    - {j['title'][:60]}")

    print(f"\n{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
