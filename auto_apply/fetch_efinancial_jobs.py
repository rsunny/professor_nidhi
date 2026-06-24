"""eFinancialCareers Job Scraper — Browser-based fetching.

Searches eFC for relevant finance/operations jobs in London,
extracts job data from search results, and saves to JSON.

Usage:
    python3 -u fetch_efinancial_jobs.py
"""

import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "data"
EFC_STORAGE_FILE = DATA_DIR / "efinancial_storage_state.json"
EFC_JOBS_RAW_FILE = DATA_DIR / "efc_jobs_raw.json"
EFC_PROGRESS_FILE = DATA_DIR / "efc_scrape_progress.json"

EMAIL = os.getenv("EFINANCE_EMAIL", "")
PASSWORD = os.getenv("EFINANCE_PASSWORD", "")

# Search queries to cover relevant roles
SEARCH_QUERIES = [
    # Core operations roles
    {"keywords": "trade operations", "location": "London"},
    {"keywords": "middle office", "location": "London"},
    {"keywords": "settlement", "location": "London"},
    {"keywords": "reconciliation", "location": "London"},
    {"keywords": "trade support", "location": "London"},
    {"keywords": "post trade", "location": "London"},
    # Finance analyst roles
    {"keywords": "finance analyst operations", "location": "London"},
    {"keywords": "investment operations", "location": "London"},
    {"keywords": "fund operations", "location": "London"},
    {"keywords": "treasury operations", "location": "London"},
    # Broader finance
    {"keywords": "operations analyst finance", "location": "London"},
    {"keywords": "corporate actions", "location": "London"},
    {"keywords": "clearing settlement", "location": "London"},
    {"keywords": "prime brokerage", "location": "London"},
    {"keywords": "fixed income operations", "location": "London"},
    {"keywords": "derivatives operations", "location": "London"},
    {"keywords": "client operations finance", "location": "London"},
    {"keywords": "asset servicing", "location": "London"},
]

MAX_PAGES_PER_QUERY = 10  # Max pages to scrape per search


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def login(page: Page) -> bool:
    """Login to eFinancialCareers."""
    await page.goto("https://www.efinancialcareers.co.uk/login", wait_until="domcontentloaded")
    await asyncio.sleep(3)

    if "/login" not in page.url.lower():
        print("  Already logged in!")
        return True

    try:
        await page.fill('#email', EMAIL)
        await asyncio.sleep(0.5)
        await page.fill('#password', PASSWORD)
        await asyncio.sleep(0.5)
        await page.click('button.submit')
        await asyncio.sleep(5)

        if "/login" not in page.url.lower():
            print("  Logged in successfully!")
            state = await page.context.storage_state()
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            EFC_STORAGE_FILE.write_text(json.dumps(state))
            return True
        else:
            print("  Login failed")
            return False
    except Exception as e:
        print(f"  Login error: {e}")
        return False


# ---------------------------------------------------------------------------
# Job Extraction
# ---------------------------------------------------------------------------

async def extract_jobs_from_page(page: Page) -> list[dict]:
    """Extract job listings from current search results page."""
    jobs = await page.evaluate("""() => {
        const results = [];
        // eFC job cards are typically in a list/grid
        const cards = document.querySelectorAll(
            '[data-test="job-card"], ' +
            '.job-card, ' +
            '[class*="JobCard"], ' +
            '[class*="job-card"], ' +
            'article[class*="job"], ' +
            '[data-gtm-category*="Job"], ' +
            '.search-result, ' +
            '[class*="SearchResult"]'
        );

        for (const card of cards) {
            try {
                // Extract title
                const titleEl = card.querySelector(
                    'h2 a, h3 a, [class*="title"] a, [data-test="job-title"] a, ' +
                    'a[class*="title"], a[class*="Title"]'
                );
                const title = titleEl ? titleEl.innerText.trim() : '';
                const url = titleEl ? titleEl.href : '';

                // Extract company
                const companyEl = card.querySelector(
                    '[class*="company"], [class*="Company"], ' +
                    '[data-test="company"], [class*="employer"]'
                );
                const company = companyEl ? companyEl.innerText.trim() : '';

                // Extract location
                const locationEl = card.querySelector(
                    '[class*="location"], [class*="Location"], ' +
                    '[data-test="location"]'
                );
                const location = locationEl ? locationEl.innerText.trim() : '';

                // Extract salary
                const salaryEl = card.querySelector(
                    '[class*="salary"], [class*="Salary"], ' +
                    '[data-test="salary"]'
                );
                const salary = salaryEl ? salaryEl.innerText.trim() : '';

                // Extract date posted
                const dateEl = card.querySelector(
                    '[class*="date"], [class*="Date"], ' +
                    '[class*="posted"], time'
                );
                const posted = dateEl ? dateEl.innerText.trim() : '';

                if (title && url) {
                    results.push({
                        title: title.substring(0, 200),
                        url: url,
                        company: company.substring(0, 100),
                        location: location.substring(0, 100),
                        salary: salary.substring(0, 100),
                        posted: posted.substring(0, 50),
                    });
                }
            } catch (e) {}
        }
        return results;
    }""")
    return jobs


async def extract_jobs_fallback(page: Page) -> list[dict]:
    """Fallback extraction using broader selectors."""
    jobs = await page.evaluate("""() => {
        const results = [];
        // Find all links that look like job URLs
        const links = document.querySelectorAll('a[href*="/jobs/"]');
        const seen = new Set();

        for (const link of links) {
            const href = link.href;
            // Filter for actual job links (not navigation)
            if (href.includes('/jobs/') && href.includes('-') &&
                !href.includes('/jobs?') && !href.includes('enableVectorSearch') &&
                !seen.has(href)) {
                seen.add(href);
                const title = link.innerText.trim();
                if (title && title.length > 5 && title.length < 200) {
                    // Try to find company/location near this link
                    const parent = link.closest('[class*="card"], [class*="result"], article, li, tr') || link.parentElement;
                    let company = '';
                    let location = '';
                    let salary = '';
                    if (parent) {
                        const texts = parent.innerText.split('\\n').map(t => t.trim()).filter(t => t.length > 0);
                        // Heuristic: first line after title is usually company
                        const titleIdx = texts.findIndex(t => t.includes(title.substring(0, 20)));
                        if (titleIdx >= 0 && titleIdx + 1 < texts.length) {
                            company = texts[titleIdx + 1].substring(0, 100);
                        }
                    }
                    results.push({
                        title: title.substring(0, 200),
                        url: href,
                        company: company,
                        location: location,
                        salary: salary,
                        posted: '',
                    });
                }
            }
        }
        return results;
    }""")
    return jobs


async def get_total_results(page: Page) -> int:
    """Get total number of search results from the page."""
    total = await page.evaluate("""() => {
        // Look for "X jobs found" or similar text
        const el = document.querySelector(
            '[class*="result-count"], [class*="ResultCount"], ' +
            '[class*="total"], [data-test*="count"], ' +
            '[class*="job-count"], [class*="JobCount"]'
        );
        if (el) {
            const match = el.innerText.match(/(\\d[\\d,]+)/);
            if (match) return parseInt(match[1].replace(',', ''));
        }
        // Try body text
        const body = document.body.innerText;
        const match = body.match(/(\\d[\\d,]+)\\s*(?:jobs?|results?|positions?)\\s*(?:found|available|matching)/i);
        if (match) return parseInt(match[1].replace(',', ''));
        return 0;
    }""")
    return total


# ---------------------------------------------------------------------------
# Search & Scrape
# ---------------------------------------------------------------------------

async def scroll_and_load_all(page: Page) -> int:
    """Scroll down repeatedly to trigger infinite scroll / lazy loading."""
    prev_count = 0
    no_change_count = 0

    for scroll_attempt in range(30):  # Max 30 scroll attempts
        # Count current job links
        current_count = await page.evaluate("""() => {
            return document.querySelectorAll('a[href*="/jobs/"]').length;
        }""")

        if current_count == prev_count:
            no_change_count += 1
            if no_change_count >= 3:
                break  # No new content after 3 scrolls
        else:
            no_change_count = 0

        prev_count = current_count

        # Scroll to bottom
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(random.uniform(1.5, 2.5))

        # Also try clicking "Show more" / "Load more" buttons
        load_more = page.locator(
            'button:has-text("Show more"), button:has-text("Load more"), '
            'a:has-text("Show more"), a:has-text("Load more"), '
            'button:has-text("View more"), a:has-text("View more"), '
            '[class*="load-more"], [class*="show-more"]'
        )
        try:
            if await load_more.first.is_visible(timeout=1000):
                await load_more.first.click()
                await asyncio.sleep(random.uniform(2, 3))
                no_change_count = 0  # Reset since we clicked something
        except Exception:
            pass

    return prev_count


async def search_and_scrape(page: Page, query: dict) -> list[dict]:
    """Run a search query and scrape all result pages."""
    keywords = query["keywords"]
    location = query.get("location", "London")

    # Build search URL — try with pageSize param to get more results
    base_url = f"https://www.efinancialcareers.co.uk/jobs?keywords={keywords.replace(' ', '+')}&location={location}"

    print(f"\n  Search: '{keywords}' in {location}")
    print(f"    URL: {base_url}")

    all_jobs = []
    page_num = 1

    while page_num <= MAX_PAGES_PER_QUERY:
        # Use pageNumber param for pagination
        search_url = f"{base_url}&pageNumber={page_num}" if page_num > 1 else base_url

        await page.goto(search_url, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(3, 5))

        if page_num == 1:
            # Get total results on first page
            total = await get_total_results(page)
            print(f"    Total results: {total}")

        # Scroll to load all results on this page
        await scroll_and_load_all(page)

        # Extract jobs from current page
        jobs = await extract_jobs_from_page(page)

        if not jobs:
            # Try fallback extraction
            jobs = await extract_jobs_fallback(page)

        if not jobs:
            print(f"    Page {page_num}: No jobs found — stopping")
            break

        # Deduplicate within this query
        new_jobs = []
        seen_urls = {j["url"] for j in all_jobs}
        for job in jobs:
            if job["url"] not in seen_urls:
                job["search_query"] = keywords
                new_jobs.append(job)
                seen_urls.add(job["url"])

        if not new_jobs:
            print(f"    Page {page_num}: No new unique jobs — stopping")
            break

        all_jobs.extend(new_jobs)
        print(f"    Page {page_num}: {len(new_jobs)} new jobs (total: {len(all_jobs)})")

        # Check if we likely have more pages
        if len(jobs) < 10:
            print(f"    Only {len(jobs)} jobs on page — likely last page")
            break

        page_num += 1
        await asyncio.sleep(random.uniform(1, 2))

    return all_jobs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("  eFinancialCareers Job Scraper")
    print("=" * 60, flush=True)

    if not EMAIL or not PASSWORD:
        print("  ERROR: EFINANCE_EMAIL/EFINANCE_PASSWORD not set")
        return

    # Load progress if resuming
    all_jobs = []
    completed_queries = set()
    if EFC_PROGRESS_FILE.exists():
        progress = json.loads(EFC_PROGRESS_FILE.read_text())
        all_jobs = progress.get("jobs", [])
        completed_queries = set(progress.get("completed_queries", []))
        print(f"  Resuming: {len(all_jobs)} jobs already scraped, {len(completed_queries)} queries done")

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

        # Login
        if not await login(page):
            await browser.close()
            return

        # Run each search query
        seen_urls = {j["url"] for j in all_jobs}

        for i, query in enumerate(SEARCH_QUERIES):
            query_key = f"{query['keywords']}|{query.get('location', 'London')}"
            if query_key in completed_queries:
                print(f"\n  [{i+1}/{len(SEARCH_QUERIES)}] SKIP (already done): '{query['keywords']}'")
                continue

            print(f"\n  [{i+1}/{len(SEARCH_QUERIES)}] Searching...")

            try:
                jobs = await search_and_scrape(page, query)

                # Deduplicate against existing
                new_count = 0
                for job in jobs:
                    if job["url"] not in seen_urls:
                        all_jobs.append(job)
                        seen_urls.add(job["url"])
                        new_count += 1

                print(f"    Added {new_count} unique jobs (total unique: {len(all_jobs)})")

            except Exception as e:
                print(f"    ERROR: {str(e)[:100]}")

            # Mark query as done
            completed_queries.add(query_key)

            # Save progress after each query
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            EFC_PROGRESS_FILE.write_text(json.dumps({
                "jobs": all_jobs,
                "completed_queries": list(completed_queries),
            }))

            # Delay between searches
            await asyncio.sleep(random.uniform(3, 6))

        # Save final results
        EFC_JOBS_RAW_FILE.write_text(json.dumps(all_jobs, indent=2))

        print(f"\n{'=' * 60}")
        print(f"  SCRAPING COMPLETE")
        print(f"  Total unique jobs: {len(all_jobs)}")
        print(f"  Saved to: {EFC_JOBS_RAW_FILE}")

        # Print breakdown by search query
        by_query = {}
        for j in all_jobs:
            q = j.get("search_query", "unknown")
            by_query[q] = by_query.get(q, 0) + 1
        print(f"\n  Jobs per query:")
        for q, count in sorted(by_query.items(), key=lambda x: -x[1]):
            print(f"    {q}: {count}")

        print(f"{'=' * 60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
