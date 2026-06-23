"""Fast Reed Filter — HTTP fetch descriptions + AI relevance check (no browser).

Reads reed_jobs_fetched.json to get URLs with apply buttons,
fetches their descriptions via HTTP, runs AI relevance filter,
and outputs reed_jobs_ready_to_apply.json for browser apply.

Usage:
    python3 -u filter_reed_fast.py
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

from config import DATA_DIR
from ai_navigator import get_client

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REED_FETCHED_FILE = DATA_DIR / "reed_jobs_fetched.json"
REED_READY_FILE = DATA_DIR / "reed_jobs_ready_to_apply.json"
REED_SESSION_FILE = DATA_DIR / "reed_storage_state.json"

MAX_CONCURRENT = 5
BATCH_SIZE = 25


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
    """Extract job description text from Reed page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    desc_el = soup.find("span", {"itemprop": "description"}) or soup.find("div", class_=re.compile(r"description|job-details|job_description"))
    if desc_el:
        return desc_el.get_text(separator=" ", strip=True)[:3000]
    main = soup.find("main") or soup.find("div", {"id": "main"})
    if main:
        return main.get_text(separator=" ", strip=True)[:3000]
    return ""


async def fetch_description(session: aiohttp.ClientSession, url: str, semaphore: asyncio.Semaphore) -> tuple[str, str]:
    """Fetch a single page and return (url, description)."""
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


async def fetch_all_descriptions(urls: list[str]) -> dict[str, str]:
    """Fetch descriptions for all URLs. Returns {url: description}."""
    cookies = get_reed_cookies()
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results = {}

    async with aiohttp.ClientSession(cookies=cookies, headers=headers) as session:
        total = len(urls)
        for batch_start in range(0, total, BATCH_SIZE):
            batch = urls[batch_start:batch_start + BATCH_SIZE]
            tasks = [fetch_description(session, url, semaphore) for url in batch]
            batch_results = await asyncio.gather(*tasks)

            for url, desc in batch_results:
                results[url] = desc

            processed = min(batch_start + BATCH_SIZE, total)
            with_desc = sum(1 for d in results.values() if len(d) > 100)
            print(f"  Fetched: {processed}/{total} | With description: {with_desc}", flush=True)

            # Check for rate limiting
            empty_count = sum(1 for _, d in batch_results if not d)
            if empty_count == len(batch):
                print(f"  WARNING: All empty in batch. Waiting 15s...", flush=True)
                await asyncio.sleep(15)
            else:
                await asyncio.sleep(random.uniform(1.5, 3))

    return results


# ---------------------------------------------------------------------------
# AI relevance filter
# ---------------------------------------------------------------------------

def check_relevance(client, title: str, description: str) -> tuple[bool, str]:
    """Check if job is relevant for Nidhi."""
    if not description or len(description) < 50:
        return False, "No description available"

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
{description[:2000]}

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
            return True, answer
        return False, answer
    except Exception as e:
        return True, f"AI error ({e}), defaulting to relevant"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("  REED FAST FILTER: Fetch Descriptions + AI Check")
    print("=" * 60, flush=True)

    # Load fetched data (has_apply_button jobs)
    fetched = json.loads(REED_FETCHED_FILE.read_text())
    has_apply = [j for j in fetched if j.get("has_apply_button")]
    print(f"  Jobs with apply button: {len(has_apply)}")

    # Step 1: Fetch descriptions via HTTP
    print(f"\n  STEP 1: Fetching descriptions for {len(has_apply)} jobs...\n", flush=True)

    urls = [j["url"] for j in has_apply]
    descriptions = await fetch_all_descriptions(urls)

    with_desc = sum(1 for d in descriptions.values() if len(d) > 100)
    print(f"\n  Descriptions fetched: {with_desc}/{len(has_apply)}")

    # Step 2: AI relevance filter
    jobs_with_desc = [(j, descriptions.get(j["url"], "")) for j in has_apply if len(descriptions.get(j["url"], "")) > 100]
    print(f"\n  STEP 2: AI relevance check on {len(jobs_with_desc)} jobs...\n", flush=True)

    client = get_client()
    relevant_jobs = []

    for i, (job, desc) in enumerate(jobs_with_desc):
        title = job.get("title", "")
        is_relevant, reason = check_relevance(client, title, desc)

        if is_relevant:
            relevant_jobs.append({
                "url": job["url"],
                "title": title,
                "company": job.get("company", ""),
                "relevance_reason": reason,
            })
            print(f"    [{i+1}] RELEVANT: {title[:60]}")

        if (i + 1) % 20 == 0:
            print(f"    --- Checked: {i+1}/{len(jobs_with_desc)} | Relevant: {len(relevant_jobs)} ---", flush=True)

    # Save results
    REED_READY_FILE.write_text(json.dumps(relevant_jobs, indent=2))

    print(f"\n{'=' * 60}")
    print(f"  COMPLETE")
    print(f"  Total checked: {len(jobs_with_desc)}")
    print(f"  Relevant & ready to apply: {len(relevant_jobs)}")
    print(f"  Saved to: {REED_READY_FILE}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
