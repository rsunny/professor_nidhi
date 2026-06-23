"""Fetch 500+ jobs from Reed.co.uk — direct DOM scraping with pagination.
Reed shows 25 jobs per page and is easy to paginate."""
import asyncio
import json
import random
import re
import sys
sys.path.insert(0, '.')

from urllib.parse import quote_plus
from playwright.async_api import async_playwright
from config import STORAGE_STATE, DATA_DIR
from browser import create_browser_context, ensure_logged_in


REED_QUERIES = [
    "trade operations analyst",
    "trade support analyst",
    "middle office analyst",
    "settlement analyst finance",
    "operations analyst investment bank",
    "derivatives operations",
    "fixed income operations",
    "reconciliation analyst finance",
    "fund operations analyst",
    "securities operations",
    "clearing settlement",
    "treasury analyst",
    "FP&A analyst",
    "finance analyst",
    "risk analyst",
    "compliance analyst",
    "portfolio analyst",
    "credit analyst",
    "investment analyst",
    "financial reporting analyst",
    "regulatory reporting",
    "fund accounting",
    "asset management operations",
    "banking operations",
    "trade finance",
    "corporate actions analyst",
    "equity operations",
    "collateral management",
    "prime brokerage",
    "hedge fund operations",
    "private equity operations",
    "custody operations",
    "payments operations",
    "capital markets operations",
    "structured products",
    "FX operations",
    "loan operations",
    "valuations analyst",
    "pricing analyst",
    "client operations",
    "financial analyst London",
    "operations associate finance",
    "trade control",
    "middle office associate",
    "treasury operations",
]

MIN_SALARY = 50000  # Lowered from 60k per user request


async def scrape_reed_page(page) -> list[dict]:
    """Extract job listings from Reed search results page."""
    jobs = await page.evaluate("""() => {
        const articles = document.querySelectorAll(
            'article[data-qa="job-card"], ' +
            '.job-result-card, ' +
            '[data-qa="search-results-list"] article, ' +
            '.results .card'
        );
        const results = [];
        for (const article of articles) {
            const link = article.querySelector('a[href*="/jobs/"]');
            if (!link) continue;

            let href = link.href || link.getAttribute('href') || '';
            if (href.startsWith('/')) href = 'https://www.reed.co.uk' + href;

            const title = (link.innerText || link.textContent || '').trim().split('\\n')[0].trim();

            // Company
            const compEl = article.querySelector(
                '[data-qa="job-card-company"], ' +
                '.company-name, ' +
                '.job-result-heading__posted-by a'
            );
            const company = compEl ? compEl.innerText.trim() : '';

            // Location
            const locEl = article.querySelector(
                '[data-qa="job-card-location"], ' +
                '.location, ' +
                '.job-result-heading__location'
            );
            const location = locEl ? locEl.innerText.trim() : '';

            // Salary
            const salEl = article.querySelector(
                '[data-qa="job-card-salary"], ' +
                '.salary, ' +
                '.job-result-heading__salary'
            );
            const salary = salEl ? salEl.innerText.trim() : '';

            if (href && title && href.includes('/jobs/')) {
                results.push({url: href, title, company, location, salary});
            }
        }
        return results;
    }""")
    return jobs


async def fetch_reed_jobs():
    """Fetch 500+ jobs from Reed.co.uk."""
    print("=" * 60)
    print("  FETCHING 500+ REED.CO.UK JOBS")
    print(f"  Queries: {len(REED_QUERIES)}")
    print(f"  Min salary: £{MIN_SALARY:,}")
    print("=" * 60, flush=True)

    async with async_playwright() as playwright:
        browser, context = await create_browser_context(playwright)
        page = await context.new_page()

        all_jobs = []
        seen_urls = set()

        for idx, query in enumerate(REED_QUERIES):
            print(f"\n  [{idx+1}/{len(REED_QUERIES)}] '{query}'", flush=True)

            # Reed URL pattern: /jobs/{query}-jobs-in-london?salaryfrom=50000
            slug = query.replace(' ', '-').lower()
            base_url = f"https://www.reed.co.uk/jobs/{slug}-jobs-in-london?salaryfrom={MIN_SALARY}&sortby=DisplayDate"

            try:
                await page.goto(base_url, wait_until="domcontentloaded")
                await asyncio.sleep(random.uniform(4, 8))

                # Check total results
                try:
                    heading = await page.locator('h1, .search-results__header').first.inner_text(timeout=5000)
                    numbers = re.findall(r'[\d,]+', heading)
                    total = int(numbers[0].replace(',', '')) if numbers else 0
                    print(f"    Total results: {total}", flush=True)
                except Exception:
                    total = 0

                # Scrape pages (Reed uses ?pageno= for pagination)
                max_pages = min(10, (total // 25) + 1) if total > 0 else 3

                for page_num in range(1, max_pages + 1):
                    if page_num > 1:
                        page_url = base_url + f"&pageno={page_num}"
                        await page.goto(page_url, wait_until="domcontentloaded")
                        await asyncio.sleep(random.uniform(3, 6))

                    page_jobs = await scrape_reed_page(page)

                    new_count = 0
                    for job in page_jobs:
                        if job['url'] not in seen_urls:
                            seen_urls.add(job['url'])
                            job['source'] = 'reed'
                            job['search_query'] = query
                            all_jobs.append(job)
                            new_count += 1

                    print(f"    Page {page_num}: {len(page_jobs)} cards, {new_count} new", flush=True)

                    if len(page_jobs) == 0:
                        break

                    await asyncio.sleep(random.uniform(2, 5))

            except Exception as e:
                print(f"    ERROR: {str(e)[:60]}", flush=True)

            print(f"    Total unique so far: {len(all_jobs)}", flush=True)

            # Delay between queries
            await asyncio.sleep(random.uniform(8, 15))

            # Longer break every 10 queries
            if (idx + 1) % 10 == 0 and idx < len(REED_QUERIES) - 1:
                long_delay = random.uniform(30, 60)
                print(f"\n  --- Break {long_delay:.0f}s ---\n", flush=True)
                await asyncio.sleep(long_delay)

            # Save progress every 10 queries
            if (idx + 1) % 10 == 0:
                progress_path = DATA_DIR / "jobs_reed_raw.json"
                with open(progress_path, 'w') as f:
                    json.dump(all_jobs, f, indent=2, default=str)
                print(f"  [saved progress: {len(all_jobs)} jobs]", flush=True)

        # Final save
        reed_path = DATA_DIR / "jobs_reed_raw.json"
        with open(reed_path, 'w') as f:
            json.dump(all_jobs, f, indent=2, default=str)

        print(f"\n{'=' * 60}")
        print(f"  DONE: {len(all_jobs)} unique Reed jobs fetched")
        print(f"  Saved to: {reed_path}")
        print(f"{'=' * 60}", flush=True)

        # Update categorized file
        cat_path = DATA_DIR / "jobs_categorized.json"
        if cat_path.exists():
            with open(cat_path) as f:
                categorized = json.load(f)
        else:
            categorized = {"linkedin_easy_apply": [], "external_by_domain": {}}

        # Add IDs
        for i, job in enumerate(all_jobs):
            job['id'] = 1000 + i  # Start from 1000 to avoid ID conflicts

        categorized["external_by_domain"]["reed"] = all_jobs
        with open(cat_path, 'w') as f:
            json.dump(categorized, f, indent=2, default=str)
        print(f"  Updated jobs_categorized.json with {len(all_jobs)} Reed jobs")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(fetch_reed_jobs())
