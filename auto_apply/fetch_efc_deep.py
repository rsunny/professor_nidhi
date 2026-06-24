"""eFinancialCareers Deep Scraper — Uses the exact search URL with proper filters.

Intercepts API calls that eFC makes when scrolling to get all 1,321 jobs.
Falls back to aggressive scrolling if API interception doesn't work.

Usage:
    python3 -u fetch_efc_deep.py
"""

import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, quote

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "data"
EFC_STORAGE_FILE = DATA_DIR / "efinancial_storage_state.json"
EFC_DEEP_JOBS_FILE = DATA_DIR / "efc_jobs_deep.json"
EFC_DEEP_PROGRESS_FILE = DATA_DIR / "efc_deep_progress.json"

# The exact URL from the user with proper filters
BASE_URL = (
    "https://www.efinancialcareers.co.uk/jobs/in-london%2C-uk"
    "?location=London%2C+UK"
    "&latitude=51.50721"
    "&longitude=-0.12758"
    "&countryCode=GB"
    "&locationPrecision=City"
    "&radius=40"
    "&radiusUnit=km"
    "&pageSize=15"
    "&filters.positionType=PERMANENT"
    "&filters.employmentType=FULL_TIME"
    "&filters.sectors=ACCOUNTING_FINANCE%7CASSET_MANAGEMENT%7COPERATIONS%7CRISK_MANAGEMENT%7CPRIVATE_BANKING_WEALTH_MANAGEMENT%7CTRADING%7CHEDGE_FUNDS%7CPRIVATE_EQUITY_VENTURE_CAPITAL%7CFINTECH%7CCOMMODITIES%7CEQUITIES%7CDERIVATIVES%7CCAPITAL_MARKETS%7CINVESTMENT_CONSULTING%7CFX_MONEY_MARKETS%7CCREDIT"
    "&filters.seniority=JUNIOR%7CANALYST%7CASSOCIATE_MID_LEVEL"
    "&currencyCode=GBP"
    "&language=en"
    "&includeUnspecifiedSalary=true"
    "&enableVectorSearch=true"
)


# ---------------------------------------------------------------------------
# API Interception
# ---------------------------------------------------------------------------

async def intercept_api_jobs(page: Page) -> list[dict]:
    """Intercept the JSON API calls eFC makes when loading job results."""
    api_jobs = []

    async def handle_response(response):
        """Capture API responses that contain job data."""
        url = response.url
        # eFC's API endpoint for job searches
        if ("/api/" in url or "gateway" in url or "search" in url) and response.status == 200:
            try:
                content_type = response.headers.get("content-type", "")
                if "json" in content_type:
                    data = await response.json()
                    # Try to find jobs array in response
                    jobs_data = None
                    if isinstance(data, list):
                        jobs_data = data
                    elif isinstance(data, dict):
                        for key in ["jobs", "results", "data", "items", "content", "hits"]:
                            if key in data and isinstance(data[key], list):
                                jobs_data = data[key]
                                break
                        # Nested
                        if not jobs_data and "data" in data and isinstance(data["data"], dict):
                            for key in ["jobs", "results", "items"]:
                                if key in data["data"] and isinstance(data["data"][key], list):
                                    jobs_data = data["data"][key]
                                    break

                    if jobs_data and len(jobs_data) > 0:
                        for item in jobs_data:
                            if isinstance(item, dict) and ("title" in item or "name" in item or "jobTitle" in item):
                                api_jobs.append(item)
            except Exception:
                pass

    page.on("response", handle_response)
    return api_jobs


# ---------------------------------------------------------------------------
# Scroll-based extraction
# ---------------------------------------------------------------------------

async def scroll_and_extract_all(page: Page, target_count: int = 1321) -> list[dict]:
    """Aggressively scroll to load all results."""
    print(f"    Scrolling to load all {target_count} jobs...")

    prev_count = 0
    no_change_count = 0
    max_no_change = 10  # Be more patient

    for scroll_num in range(200):  # Up to 200 scroll attempts
        # Count current job links
        current_count = await page.evaluate("""() => {
            return document.querySelectorAll('a[href*=".id"]').length;
        }""")

        if current_count >= target_count:
            print(f"    Reached target: {current_count} links loaded")
            break

        if current_count == prev_count:
            no_change_count += 1
            if no_change_count >= max_no_change:
                print(f"    Scroll stalled at {current_count} links after {no_change_count} attempts")
                break
        else:
            no_change_count = 0
            if scroll_num % 10 == 0:
                print(f"    Scroll {scroll_num}: {current_count} links loaded...", flush=True)

        prev_count = current_count

        # Scroll to bottom
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(random.uniform(1, 2))

        # Try clicking load more / show more buttons
        try:
            load_more = page.locator(
                'button:has-text("Show more"), button:has-text("Load more"), '
                'button:has-text("View more"), a:has-text("Show more"), '
                '[class*="load-more"], [class*="show-more"], '
                '[class*="LoadMore"], [class*="ShowMore"]'
            )
            if await load_more.first.is_visible(timeout=1000):
                await load_more.first.click()
                await asyncio.sleep(random.uniform(2, 3))
                no_change_count = 0
        except Exception:
            pass

    # Final count
    final_count = await page.evaluate("""() => {
        return document.querySelectorAll('a[href*=".id"]').length;
    }""")
    print(f"    Final: {final_count} job links in DOM")
    return final_count


async def extract_jobs_from_dom(page: Page) -> list[dict]:
    """Extract all job data from the loaded DOM."""
    jobs = await page.evaluate("""() => {
        const results = [];
        const seen = new Set();
        const links = document.querySelectorAll('a[href*=".id"]');

        for (const link of links) {
            const href = link.href;
            if (!href.includes('.id') || seen.has(href)) continue;
            if (href.includes('/login') || href.includes('/register')) continue;
            seen.add(href);

            const title = link.innerText.trim();
            if (!title || title.length < 3 || title.length > 300) continue;
            if (title.includes('Sign in') || title.includes('Register') || title.includes('Cookie')) continue;

            // Get parent card
            const card = link.closest('[class*="card"], [class*="Card"], [class*="result"], [class*="Result"], article, li, [class*="job-item"], [class*="JobItem"]') || link.parentElement?.parentElement || link.parentElement;

            let company = '';
            let location = '';
            let salary = '';
            let posted = '';
            let jobType = '';

            if (card) {
                const textNodes = card.querySelectorAll('span, p, div, a');
                for (const node of textNodes) {
                    const text = (node.innerText || '').trim();
                    if (!text || text === title || text.length > 200) continue;

                    if (!company && node !== link && !text.includes('London') &&
                        !text.includes('£') && !text.includes('ago') &&
                        !text.includes('Permanent') && !text.includes('Full') &&
                        text.length > 2 && text.length < 80) {
                        // Check if it's likely a company name (usually right after title)
                        const isCompanyEl = node.matches('[class*="company"], [class*="Company"], [class*="employer"], [class*="Employer"]') ||
                                           (node.tagName === 'A' && (node.href || '').includes('/company/'));
                        if (isCompanyEl || (!company && !location && !salary)) {
                            company = text.substring(0, 100);
                        }
                    }

                    if (text.includes('London') || text.includes('United Kingdom') || text.includes(', UK')) {
                        location = text.substring(0, 150);
                    }
                    if (text.includes('£') || text.match(/\\d+[kK]\\s*[-–]\\s*\\d+[kK]/)) {
                        salary = text.substring(0, 100);
                    }
                    if (text.includes('ago') || text.includes('day') || text.includes('week') || text.includes('month')) {
                        if (text.length < 50) posted = text;
                    }
                    if (text.includes('Permanent') || text.includes('Full-time') || text.includes('Contract')) {
                        jobType = text.substring(0, 50);
                    }
                }
            }

            results.push({
                title: title.substring(0, 200),
                url: href,
                company: company,
                location: location || 'London, UK',
                salary: salary,
                posted: posted,
                job_type: jobType,
            });
        }
        return results;
    }""")
    return jobs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("  eFinancialCareers DEEP Scraper")
    print("  Target: 1,321 London finance jobs (Permanent, Full-time)")
    print("=" * 60, flush=True)

    # Load existing
    all_jobs = []
    seen_urls = set()

    if EFC_DEEP_PROGRESS_FILE.exists():
        progress = json.loads(EFC_DEEP_PROGRESS_FILE.read_text())
        all_jobs = progress.get("jobs", [])
        seen_urls = {j["url"] for j in all_jobs}
        print(f"  Resuming: {len(all_jobs)} jobs from previous progress")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )

        context_options = {
            "viewport": {"width": 1366, "height": 768},
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "locale": "en-GB",
            "timezone_id": "Europe/London",
        }

        if EFC_STORAGE_FILE.exists():
            context_options["storage_state"] = str(EFC_STORAGE_FILE)
            print("  Loaded saved session")

        context = await browser.new_context(**context_options)
        page = await context.new_page()

        # Set up API interception
        api_jobs = await intercept_api_jobs(page)

        # Navigate to the search page
        print(f"\n  Loading search page...")
        await page.goto(BASE_URL, wait_until="domcontentloaded")
        await asyncio.sleep(5)

        # Get the actual count shown on page
        count_text = await page.evaluate("""() => {
            const body = document.body.innerText;
            const match = body.match(/(\\d[\\d,]*)\\s*(?:jobs?|results?|positions?)/i);
            return match ? match[0] : 'count not found';
        }""")
        print(f"  Page shows: {count_text}")

        # Scroll to load all results
        await scroll_and_extract_all(page, target_count=1321)

        # Extract from DOM
        jobs = await extract_jobs_from_dom(page)
        print(f"\n  Extracted {len(jobs)} jobs from DOM")

        # Also check API intercepts
        if api_jobs:
            print(f"  API interception captured: {len(api_jobs)} job records")

        # Merge with existing
        new_count = 0
        for j in jobs:
            if j["url"] not in seen_urls:
                all_jobs.append(j)
                seen_urls.add(j["url"])
                new_count += 1

        print(f"  New unique jobs: {new_count}")
        print(f"  Total unique: {len(all_jobs)}")

        # If we didn't get enough from scrolling, try the API approach
        if len(all_jobs) < 800 and api_jobs:
            print(f"\n  Processing {len(api_jobs)} API-intercepted jobs...")
            for item in api_jobs:
                # Try to extract standard fields
                title = item.get("title") or item.get("name") or item.get("jobTitle", "")
                url = item.get("url") or item.get("detailUrl") or item.get("link", "")
                if not url and item.get("id"):
                    url = f"https://www.efinancialcareers.co.uk/jobs/.id{item['id']}"

                if title and url and url not in seen_urls:
                    all_jobs.append({
                        "title": title[:200],
                        "url": url,
                        "company": item.get("company", {}).get("name", "") if isinstance(item.get("company"), dict) else str(item.get("company", "")),
                        "location": item.get("location", {}).get("name", "") if isinstance(item.get("location"), dict) else str(item.get("location", "")),
                        "salary": item.get("salary", ""),
                        "posted": item.get("postedDate", "") or item.get("posted", ""),
                        "job_type": "Permanent",
                        "source": "api",
                    })
                    seen_urls.add(url)
                    new_count += 1

        # If still not enough, try increasing pageSize in URL
        if len(all_jobs) < 800:
            print(f"\n  Trying larger pageSize...")
            for page_size in [50, 100, 200]:
                large_url = BASE_URL.replace("pageSize=15", f"pageSize={page_size}")
                await page.goto(large_url, wait_until="domcontentloaded")
                await asyncio.sleep(5)
                await scroll_and_extract_all(page, target_count=1321)
                extra_jobs = await extract_jobs_from_dom(page)
                extra_new = 0
                for j in extra_jobs:
                    if j["url"] not in seen_urls:
                        all_jobs.append(j)
                        seen_urls.add(j["url"])
                        extra_new += 1
                print(f"    pageSize={page_size}: {len(extra_jobs)} found, {extra_new} new (total: {len(all_jobs)})")
                if extra_new == 0:
                    break

        # Save results
        EFC_DEEP_JOBS_FILE.write_text(json.dumps(all_jobs, indent=2))
        EFC_DEEP_PROGRESS_FILE.write_text(json.dumps({"jobs": all_jobs}))

        print(f"\n{'=' * 60}")
        print(f"  COMPLETE")
        print(f"  Total unique jobs: {len(all_jobs)}")
        print(f"  Saved to: {EFC_DEEP_JOBS_FILE}")

        # Show sample
        print(f"\n  Sample jobs:")
        for j in all_jobs[:10]:
            print(f"    - {j['title'][:55]} | {j.get('company', '')[:25]} | {j.get('salary', '')}")

        # Show API intercepted data if any
        if api_jobs:
            print(f"\n  API data sample:")
            for item in api_jobs[:3]:
                print(f"    {json.dumps(item, indent=2)[:200]}")

        print(f"{'=' * 60}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
