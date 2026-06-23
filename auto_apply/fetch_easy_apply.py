"""Fetch 500+ LinkedIn Easy Apply jobs — direct DOM scraping with proper pagination.
No AI filtering during scrape — collect everything, filter locally after."""
import asyncio
import json
import random
import sys
sys.path.insert(0, '.')

from urllib.parse import urlencode
from playwright.async_api import async_playwright
from config import STORAGE_STATE, DATA_DIR
from browser import create_browser_context, ensure_logged_in


# Expanded search queries to maximize coverage
SEARCH_QUERIES = [
    # Core operations roles
    "Trade Operations Analyst",
    "Trade Support Analyst",
    "Middle Office Analyst",
    "Settlement Analyst",
    "Operations Analyst finance",
    "Trade Processing Analyst",
    "Post Trade Analyst",
    "Fixed Income Operations",
    "Equity Operations Analyst",
    "Prime Brokerage Operations",
    "Derivatives Operations",
    "Reconciliation Analyst",
    "Securities Settlement",
    "Trade Lifecycle",
    "Middle Office Associate",
    "Trade Support Associate",
    "Operations Associate hedge fund",
    "Trade Operations Associate",
    "Equity Swaps Operations",
    "Collateral Management Analyst",
    "OTC Derivatives Operations",
    "Treasury Operations Analyst",
    "Fund Operations Analyst",
    "Investment Operations Analyst",
    "Financial Operations London",
    "Trade Control Analyst",
    "Markets Operations",
    "Securities Operations",
    "Clearing Settlement Analyst",
    # Additional broader queries
    "Finance Analyst London",
    "Risk Analyst finance",
    "Compliance Analyst financial",
    "FP&A Analyst",
    "Financial Analyst investment",
    "Portfolio Analyst",
    "Credit Analyst",
    "Fund Accounting Analyst",
    "Asset Management Operations",
    "Banking Operations Analyst",
    "Financial Reporting Analyst",
    "Regulatory Reporting Analyst",
    "Valuations Analyst",
    "Pricing Analyst finance",
    "Corporate Actions Analyst",
    "Client Operations Analyst",
    "Investment Banking Analyst",
    "Hedge Fund Operations",
    "Private Equity Operations",
    "Custody Operations",
    "Payments Operations Analyst",
    "Treasury Analyst London",
    "Trade Finance Analyst",
    "Capital Markets Operations",
    "Structured Products Operations",
    "Commodities Operations",
    "FX Operations Analyst",
    "Rates Operations",
    "Credit Operations",
    "Loan Operations Analyst",
    # Supplemental queries to push past 500
    "Back Office Analyst London",
    "Trade Confirmation Analyst",
    "Investment Operations Associate",
    "Securities Analyst London",
    "Front Office Support",
    "Market Risk Analyst",
    "Quantitative Analyst junior",
    "Financial Controller London",
    "Fund Administrator",
    "Broker Operations",
    "Settlements Operations London",
    "Post Trade Support",
    "ETF Operations",
    "Money Market Operations",
    "Repo Operations Analyst",
]

LOCATION = "London"
LOCATION_GEOID = "102257491"


async def scroll_to_load_all_cards(page, max_scrolls=15):
    """Scroll the job list container to load all lazy-loaded cards."""
    # LinkedIn lazy loads cards in the left panel
    list_container = page.locator('.jobs-search-results-list, .scaffold-layout__list')

    for i in range(max_scrolls):
        try:
            await list_container.evaluate(
                "el => el.scrollTop = el.scrollTop + el.clientHeight"
            )
        except Exception:
            # Fallback: scroll the page itself
            await page.evaluate("window.scrollBy(0, 600)")
        await asyncio.sleep(0.8)

    # Scroll back to top
    try:
        await list_container.evaluate("el => el.scrollTop = 0")
    except Exception:
        pass


async def scrape_job_cards(page) -> list[dict]:
    """Extract all job cards from current page after scrolling."""
    await scroll_to_load_all_cards(page)
    await asyncio.sleep(2)

    jobs = await page.evaluate("""() => {
        const cards = document.querySelectorAll(
            '.job-card-container, ' +
            '.jobs-search-results__list-item, ' +
            'li[data-occludable-job-id], ' +
            '.scaffold-layout__list-item, ' +
            '.jobs-search-results-list__list-item'
        );
        const results = [];
        for (const card of cards) {
            const link = card.querySelector('a[href*="/jobs/view/"]');
            if (!link) continue;

            let href = link.href || link.getAttribute('href') || '';
            // Normalize URL
            if (href.startsWith('/')) href = 'https://www.linkedin.com' + href;
            href = href.split('?')[0];

            const titleEl = card.querySelector(
                '.job-card-list__title, ' +
                '.artdeco-entity-lockup__title, ' +
                'a[data-control-name="job_card_title"]'
            ) || link;
            const title = (titleEl.innerText || titleEl.textContent || '').trim().split('\\n')[0].trim();

            const companyEl = card.querySelector(
                '.job-card-container__primary-description, ' +
                '.artdeco-entity-lockup__subtitle, ' +
                '.job-card-container__company-name, ' +
                'span.job-card-list__company-name'
            );
            const company = companyEl ? companyEl.innerText.trim().split('\\n')[0] : '';

            const locEl = card.querySelector(
                '.job-card-container__metadata-wrapper li, ' +
                '.artdeco-entity-lockup__caption, ' +
                '.job-card-container__metadata-item'
            );
            const location = locEl ? locEl.innerText.trim() : '';

            if (href && href.includes('/jobs/view/') && title) {
                results.push({url: href, title, company, location});
            }
        }
        return results;
    }""")
    return jobs


async def get_total_results(page) -> int:
    """Get the total number of results shown."""
    try:
        text = await page.locator(
            '.jobs-search-results-list__subtitle, '
            '.jobs-search-results-list__title-heading--small'
        ).first.inner_text(timeout=5000)
        import re
        numbers = re.findall(r'[\d,]+', text)
        if numbers:
            return int(numbers[0].replace(',', ''))
    except Exception:
        pass
    return 0


async def fetch_easy_apply_jobs():
    """Fetch ALL LinkedIn Easy Apply jobs with proper pagination."""
    print("=" * 60)
    print("  FETCHING 500+ LINKEDIN EASY APPLY JOBS")
    print("  (Direct DOM scraping, no AI filtering)")
    print(f"  Queries: {len(SEARCH_QUERIES)}")
    print("=" * 60, flush=True)

    # Load existing results to continue from where we left off
    easy_apply_path = DATA_DIR / "jobs_easy_apply_raw.json"
    existing_jobs = []
    if easy_apply_path.exists():
        with open(easy_apply_path) as f:
            existing_jobs = json.load(f)
        print(f"  Loaded {len(existing_jobs)} existing jobs (will skip these URLs)")

    async with async_playwright() as playwright:
        browser, context = await create_browser_context(playwright)
        page = await ensure_logged_in(context)

        all_jobs = list(existing_jobs)  # Start with existing
        seen_urls = {j['url'] for j in existing_jobs}

        for idx, query in enumerate(SEARCH_QUERIES):
            print(f"\n  [{idx+1}/{len(SEARCH_QUERIES)}] '{query}'", end="", flush=True)

            params = {
                "keywords": query,
                "location": LOCATION,
                "geoId": LOCATION_GEOID,
                "f_TPR": "r2592000",   # Last 30 days
                "f_E": "2,3,4",         # Entry/Associate/Mid-Senior
                "f_AL": "true",         # Easy Apply ONLY
                "sortBy": "R",          # Most relevant (different from first pass which used DD)
            }
            url = f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"

            try:
                await page.goto(url, wait_until="domcontentloaded")
                await asyncio.sleep(random.uniform(5, 8))

                # Always paginate up to 5 pages (don't rely on total count)
                for page_num in range(1, 6):
                    if page_num > 1:
                        page_url = url + f"&start={(page_num-1) * 25}"
                        await page.goto(page_url, wait_until="domcontentloaded")
                        await asyncio.sleep(random.uniform(4, 7))

                    page_jobs = await scrape_job_cards(page)
                    page_new = 0
                    for job in page_jobs:
                        if job['url'] not in seen_urls:
                            seen_urls.add(job['url'])
                            job['source'] = 'linkedin_easy_apply'
                            job['search_query'] = query
                            all_jobs.append(job)
                            page_new += 1

                    print(f"    Page {page_num}: {len(page_jobs)} cards, {page_new} new", flush=True)

                    # Stop if no cards found (reached end of results)
                    if len(page_jobs) == 0:
                        break

                    await asyncio.sleep(random.uniform(3, 6))

            except Exception as e:
                print(f"    ERROR: {str(e)[:60]}", flush=True)

            print(f"    Total unique so far: {len(all_jobs)}", flush=True)

            # Anti-detection delays
            delay = random.uniform(10, 20)
            await asyncio.sleep(delay)

            # Longer break every 10 queries
            if (idx + 1) % 10 == 0 and idx < len(SEARCH_QUERIES) - 1:
                long_delay = random.uniform(45, 90)
                print(f"\n  --- Break {long_delay:.0f}s (anti-detection) ---\n", flush=True)
                await asyncio.sleep(long_delay)

            # Save intermediate progress every 10 queries
            if (idx + 1) % 10 == 0:
                progress_path = DATA_DIR / "jobs_easy_apply_raw.json"
                with open(progress_path, 'w') as f:
                    json.dump(all_jobs, f, indent=2, default=str)
                print(f"  [saved progress: {len(all_jobs)} jobs]", flush=True)

        # Final save
        easy_apply_path = DATA_DIR / "jobs_easy_apply_raw.json"
        with open(easy_apply_path, 'w') as f:
            json.dump(all_jobs, f, indent=2, default=str)

        print(f"\n{'=' * 60}")
        print(f"  DONE: {len(all_jobs)} unique Easy Apply jobs fetched")
        print(f"  Saved to: {easy_apply_path}")
        print(f"{'=' * 60}", flush=True)

        # Add IDs and update categorized file
        for i, job in enumerate(all_jobs):
            job['id'] = i + 1

        cat_path = DATA_DIR / "jobs_categorized.json"
        if cat_path.exists():
            with open(cat_path) as f:
                categorized = json.load(f)
        else:
            categorized = {"linkedin_easy_apply": [], "external_by_domain": {}}

        categorized["linkedin_easy_apply"] = all_jobs
        with open(cat_path, 'w') as f:
            json.dump(categorized, f, indent=2, default=str)
        print(f"  Updated jobs_categorized.json")

        await context.storage_state(path=str(STORAGE_STATE))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(fetch_easy_apply_jobs())
