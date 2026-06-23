"""Reed Job Page Fetcher & Filter — HTTP-based (no browser needed).

Step 1: Fetch all remaining Reed job pages via HTTP requests (fast, parallel)
Step 2: Parse HTML to extract: description, apply button presence, external links
Step 3: AI relevance filter only on jobs that have a direct apply button
Step 4: Output a final list of jobs ready for browser-based application

Usage:
    python3 -u fetch_and_filter_reed.py
"""

import asyncio
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))

from config import DATA_DIR, OUTPUT_DIR
from ai_navigator import get_client

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REED_JOBS_FILE = DATA_DIR / "jobs_reed_raw.json"
REED_PROGRESS_FILE = DATA_DIR / "reed_progress.json"
REED_FETCHED_FILE = DATA_DIR / "reed_jobs_fetched.json"
REED_FILTERED_FILE = DATA_DIR / "reed_jobs_ready_to_apply.json"

# Concurrency for HTTP fetches (Reed blocks aggressively)
MAX_CONCURRENT = 3
BATCH_SIZE = 20

# Reed cookies from saved session
REED_SESSION_FILE = DATA_DIR / "reed_storage_state.json"

EXCLUDE_KEYWORDS = [
    "senior manager", "director", "head of", "vp ", "vice president",
    "10+ years", "10 years", "15 years", "principal", "lead architect",
    "chief", "cto", "cfo", "partner",
]


# ---------------------------------------------------------------------------
# HTTP Fetching
# ---------------------------------------------------------------------------

def get_reed_cookies() -> dict:
    """Load Reed cookies from saved Playwright storage state."""
    if not REED_SESSION_FILE.exists():
        return {}
    state = json.loads(REED_SESSION_FILE.read_text())
    cookies = {}
    for cookie in state.get("cookies", []):
        if "reed.co.uk" in cookie.get("domain", ""):
            cookies[cookie["name"]] = cookie["value"]
    return cookies


def parse_reed_page(html: str, url: str) -> dict:
    """Parse a Reed job page HTML and extract key info."""
    soup = BeautifulSoup(html, "html.parser")

    result = {
        "url": url,
        "has_apply_button": False,
        "is_expired": False,
        "is_external": False,
        "external_domain": "",
        "description": "",
        "title": "",
    }

    # Get title
    h1 = soup.find("h1")
    if h1:
        result["title"] = h1.get_text(strip=True)

    # Get job description
    desc_el = soup.find("span", {"itemprop": "description"}) or soup.find("div", class_=re.compile(r"description|job-details|job_description"))
    if desc_el:
        result["description"] = desc_el.get_text(separator=" ", strip=True)[:3000]
    else:
        # Fallback: get main content
        main = soup.find("main") or soup.find("div", {"id": "main"})
        if main:
            result["description"] = main.get_text(separator=" ", strip=True)[:3000]

    body_text = soup.get_text(separator=" ").lower()

    # Check expired
    if "this job has expired" in body_text or "no longer available" in body_text or "no longer accepting" in body_text:
        result["is_expired"] = True
        return result

    # Check for direct apply button
    apply_patterns = [
        soup.find("a", string=re.compile(r"apply for this job|apply now", re.I)),
        soup.find("button", string=re.compile(r"apply for this job|apply now", re.I)),
        soup.find("a", class_=re.compile(r"apply")),
        soup.find("button", class_=re.compile(r"apply")),
    ]
    for el in apply_patterns:
        if el:
            result["has_apply_button"] = True
            break

    # Check for external apply link
    external_patterns = [
        soup.find("a", string=re.compile(r"apply on company|apply on employer|apply on external|complete on employer", re.I)),
    ]
    for el in external_patterns:
        if el:
            result["is_external"] = True
            href = el.get("href", "")
            if href.startswith("http"):
                result["external_domain"] = href.split("/")[2] if len(href.split("/")) > 2 else ""
            break

    # Also check for "Easy Apply" badge on THIS job (not sidebar)
    # Reed marks some jobs with "Easy Apply" meaning direct apply on Reed
    job_header = soup.find("div", class_=re.compile(r"job-header|job-meta"))
    if job_header:
        header_text = job_header.get_text().lower()
        if "easy apply" in header_text:
            result["has_apply_button"] = True

    return result


async def fetch_page(session: aiohttp.ClientSession, url: str, semaphore: asyncio.Semaphore) -> dict:
    """Fetch a single Reed job page."""
    async with semaphore:
        try:
            # Small random delay per request to look human
            await asyncio.sleep(random.uniform(0.5, 1.5))
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20), allow_redirects=True) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    return parse_reed_page(html, url)
                elif resp.status == 404:
                    return {"url": url, "is_expired": True, "has_apply_button": False, "description": "", "title": "", "is_external": False, "external_domain": ""}
                elif resp.status == 403:
                    return {"url": url, "error": "HTTP 403 Forbidden (rate limited)", "has_apply_button": False, "description": "", "title": "", "is_expired": False, "is_external": False, "external_domain": ""}
                elif resp.status == 429:
                    return {"url": url, "error": "HTTP 429 Too Many Requests", "has_apply_button": False, "description": "", "title": "", "is_expired": False, "is_external": False, "external_domain": ""}
                else:
                    return {"url": url, "error": f"HTTP {resp.status}", "has_apply_button": False, "description": "", "title": "", "is_expired": False, "is_external": False, "external_domain": ""}
        except Exception as e:
            return {"url": url, "error": str(e)[:100], "has_apply_button": False, "description": "", "title": "", "is_expired": False, "is_external": False, "external_domain": ""}


async def fetch_all_pages(jobs: list[dict]) -> list[dict]:
    """Fetch all job pages concurrently."""
    cookies = get_reed_cookies()
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results = []

    async with aiohttp.ClientSession(cookies=cookies, headers=headers) as session:
        total = len(jobs)
        for batch_start in range(0, total, BATCH_SIZE):
            batch = jobs[batch_start:batch_start + BATCH_SIZE]
            tasks = [fetch_page(session, job["url"], semaphore) for job in batch]
            batch_results = await asyncio.gather(*tasks)

            # Merge original job data with fetched data
            for job, fetched in zip(batch, batch_results):
                merged = {**job, **fetched}
                results.append(merged)

            processed = min(batch_start + BATCH_SIZE, total)
            has_apply = sum(1 for r in results if r.get("has_apply_button"))
            expired = sum(1 for r in results if r.get("is_expired"))
            external = sum(1 for r in results if r.get("is_external"))
            errors = sum(1 for r in results if r.get("error"))

            print(f"  Fetched: {processed}/{total} | Has Apply: {has_apply} | Expired: {expired} | External: {external} | Errors: {errors}", flush=True)

            # Check if we're getting rate limited (too many consecutive errors)
            batch_errors = sum(1 for r in batch_results if r.get("error"))
            if batch_errors == len(batch):
                print(f"  WARNING: Entire batch failed. Reed may be rate limiting. Waiting 30s...", flush=True)
                await asyncio.sleep(30)
            elif batch_errors > len(batch) * 0.5:
                print(f"  WARNING: High error rate ({batch_errors}/{len(batch)}). Slowing down...", flush=True)
                await asyncio.sleep(10)
            else:
                # Normal delay between batches
                await asyncio.sleep(random.uniform(2, 4))

    return results


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def quick_title_filter(title: str) -> bool:
    """Fast pre-filter based on title alone."""
    t = title.lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in t:
            return False
    return True


def check_relevance_batch(client, jobs: list[dict]) -> list[dict]:
    """Use AI to batch-check relevance for jobs that have apply buttons.
    Returns jobs with 'is_relevant' and 'relevance_reason' fields added."""
    relevant_jobs = []

    for i, job in enumerate(jobs):
        title = job.get("title", "")
        desc = job.get("description", "")[:2000]

        if not desc:
            job["is_relevant"] = False
            job["relevance_reason"] = "No description found"
            continue

        prompt = f"""You are filtering jobs for Nidhi Shetty. She has:
- 5 years experience, 2.5 in financial services (Morgan Stanley Prime Brokerage)
- MSc Investment & Risk Finance (Distinction)
- Skills: trade settlement, reconciliation, middle office, Excel/VBA, Bloomberg, Python (beginner)
- Looking for: trade operations, middle office, settlement, reconciliation, finance analyst roles
- Location: London (already based there)
- Needs Skilled Worker visa sponsorship
- Open to ANY salary range

JOB TITLE: {title}

JOB DESCRIPTION (first 2000 chars):
{desc}

Is this job relevant? Consider:
1. Finance/operations/analyst area? (YES needed)
2. Appropriate seniority (entry to mid-level, NOT director/VP/10+ years)? (YES needed)
3. London-based or remote? (YES needed)
4. PERMANENT role (not short-term day-rate contract)? (YES needed)

DO NOT skip based on salary.
Reply with EXACTLY one line: RELEVANT: <reason> or SKIP: <reason>"""

        try:
            response = client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1"),
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = response.content[0].text.strip()
            if answer.startswith("RELEVANT"):
                job["is_relevant"] = True
                job["relevance_reason"] = answer
                relevant_jobs.append(job)
            else:
                job["is_relevant"] = False
                job["relevance_reason"] = answer
        except Exception as e:
            # On error, include it (conservative)
            job["is_relevant"] = True
            job["relevance_reason"] = f"AI check failed ({e}), defaulting to relevant"
            relevant_jobs.append(job)

        if (i + 1) % 10 == 0:
            rel_count = sum(1 for j in jobs[:i+1] if j.get("is_relevant"))
            print(f"    AI filtered: {i+1}/{len(jobs)} | Relevant so far: {rel_count}", flush=True)

    return relevant_jobs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("  REED JOBS: FETCH & FILTER (HTTP-based)")
    print("=" * 60, flush=True)

    # Load all Reed jobs
    all_jobs = json.loads(REED_JOBS_FILE.read_text())
    print(f"  Total Reed jobs in file: {len(all_jobs)}")

    # Load already-processed URLs (from previous browser runs)
    processed_urls = set()
    if REED_PROGRESS_FILE.exists():
        processed_urls = set(json.loads(REED_PROGRESS_FILE.read_text()))
    print(f"  Already processed (browser): {len(processed_urls)}")

    # Get remaining jobs
    remaining = [j for j in all_jobs if j["url"] not in processed_urls]

    # Quick title filter
    title_filtered = [j for j in remaining if quick_title_filter(j.get("title", ""))]
    print(f"  After title filter: {len(title_filtered)}")
    print(f"\n  STEP 1: Fetching all pages via HTTP...\n", flush=True)

    # Fetch all pages
    fetched = await fetch_all_pages(title_filtered)

    # Save fetched data
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(REED_FETCHED_FILE, "w") as f:
        # Don't save full descriptions to keep file manageable
        save_data = []
        for j in fetched:
            save_data.append({
                "url": j["url"],
                "title": j.get("title", ""),
                "company": j.get("company", ""),
                "has_apply_button": j.get("has_apply_button", False),
                "is_expired": j.get("is_expired", False),
                "is_external": j.get("is_external", False),
                "external_domain": j.get("external_domain", ""),
                "error": j.get("error", ""),
                "description_length": len(j.get("description", "")),
            })
        json.dump(save_data, f, indent=2)

    # Summary
    has_apply = [j for j in fetched if j.get("has_apply_button")]
    expired = [j for j in fetched if j.get("is_expired")]
    external = [j for j in fetched if j.get("is_external")]
    no_button = [j for j in fetched if not j.get("has_apply_button") and not j.get("is_expired") and not j.get("is_external") and not j.get("error")]
    errors = [j for j in fetched if j.get("error")]

    print(f"\n  STEP 1 COMPLETE:")
    print(f"    Has direct apply button: {len(has_apply)}")
    print(f"    Expired: {len(expired)}")
    print(f"    External only: {len(external)}")
    print(f"    No button (dead listing): {len(no_button)}")
    print(f"    Errors: {len(errors)}")

    # Log external domains
    if external:
        from collections import Counter
        domains = Counter(j.get("external_domain", "unknown") for j in external)
        print(f"\n  External redirect domains:")
        for domain, count in domains.most_common(20):
            print(f"    {domain}: {count}")

    if not has_apply:
        print("\n  No jobs with direct apply buttons found. Done.")
        return

    # STEP 2: AI relevance filter on jobs with apply buttons
    print(f"\n  STEP 2: AI relevance check on {len(has_apply)} jobs with apply buttons...\n", flush=True)

    client = get_client()
    relevant = check_relevance_batch(client, has_apply)

    print(f"\n  STEP 2 COMPLETE:")
    print(f"    Relevant & ready to apply: {len(relevant)}")

    # Save final filtered list
    ready_to_apply = []
    for j in relevant:
        ready_to_apply.append({
            "url": j["url"],
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "relevance_reason": j.get("relevance_reason", ""),
        })

    with open(REED_FILTERED_FILE, "w") as f:
        json.dump(ready_to_apply, f, indent=2)

    print(f"\n  Saved {len(ready_to_apply)} jobs to: {REED_FILTERED_FILE}")
    print(f"  These are ready for browser-based application.")
    print(f"\n{'=' * 60}")
    print(f"  SUMMARY")
    print(f"  Total fetched: {len(fetched)}")
    print(f"  Expired: {len(expired)}")
    print(f"  External: {len(external)}")
    print(f"  Has apply button: {len(has_apply)}")
    print(f"  Relevant & ready: {len(ready_to_apply)}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
