"""LinkedIn Company Job Scraper — Searches jobs at each of 500 companies via LinkedIn.

Uses our existing LinkedIn session to search for relevant roles at each company.
LinkedIn search URL format: /jobs/search/?keywords={query}&location=London&f_C={company}

Since we don't have company IDs, we'll use keyword search:
  "operations" + company name + London

Usage:
    python3 -u scrape_linkedin_companies.py
"""

import asyncio
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "output"
STORAGE_FILE = DATA_DIR / "storage_state.json"
RESULTS_FILE = DATA_DIR / "careers_scrape_results.json"
PROGRESS_FILE = DATA_DIR / "linkedin_company_scrape_progress.json"

RELEVANT_KEYWORDS = [
    r"operat", r"settlement", r"reconcil", r"middle.?office",
    r"trade.?support", r"post.?trade", r"clearing", r"custody",
    r"fund.?account", r"asset.?servic", r"corporate.?action",
    r"treasury", r"financial.?analyst", r"finance.?analyst",
    r"investment.?operat", r"fund.?operat", r"banking.?operat",
    r"transfer.?agent", r"collateral", r"payment.?(?:operat|analyst)",
    r"client.?servic", r"reporting.?analyst",
    r"risk.?analyst", r"compliance.?analyst", r"regulatory",
    r"loan.?(?:admin|operat|analyst)", r"credit.?(?:analyst|operat|control)",
    r"portfolio.?analyst", r"business.?analyst",
    r"product.?control", r"investor.?servic", r"fund.?servic",
    r"finance.?operat", r"back.?office", r"account(?:ing)?\s*(?:analyst|officer)",
    r"kyc|onboarding", r"data.?(?:analyst|quality)",
    r"project.?(?:analyst|coord)", r"change.?(?:analyst|manager)",
]

REJECT_KEYWORDS = [
    r"\bdirector\b", r"\bvp\b", r"vice.?president", r"\bhead\s+of\b",
    r"\bchief\b", r"managing.?director",
    r"software.?eng", r"\bdeveloper\b", r"\bdevops\b", r"data.?eng",
    r"machine.?learn", r"full.?stack", r"\barchitect\b",
    r"\bintern\b", r"\bapprentice\b",
]


def is_relevant(title: str) -> bool:
    t = title.lower()
    for p in REJECT_KEYWORDS:
        if re.search(p, t):
            return False
    for p in RELEVANT_KEYWORDS:
        if re.search(p, t):
            return True
    return False


# ---------------------------------------------------------------------------
# LinkedIn search
# ---------------------------------------------------------------------------

async def search_linkedin_jobs(page: Page, company: str, search_terms: list[str]) -> list[dict]:
    """Search LinkedIn jobs for a company with relevant keywords."""
    jobs = []
    seen_titles = set()
    
    for term in search_terms:
        query = f'"{company}" {term}'
        url = (
            f"https://www.linkedin.com/jobs/search/"
            f"?keywords={quote_plus(query)}"
            f"&location=London%2C%20United%20Kingdom"
            f"&f_TP=1%2C2%2C3%2C4"  # Posted in last month
            f"&f_JT=F"  # Full-time
            f"&sortBy=R"  # Relevance
        )
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(random.uniform(3, 5))
            
            # Check for rate limit
            body_text = await page.inner_text("body")
            if "we've hit a snag" in body_text.lower() or "unusual" in body_text.lower():
                print(f"      Rate limited — waiting 60s")
                await asyncio.sleep(60)
                continue
            
            # Extract job cards
            extracted = await page.evaluate("""(company) => {
                const results = [];
                const cards = document.querySelectorAll(
                    '.jobs-search-results__list-item, ' +
                    '[class*="job-card-container"], ' +
                    '[class*="jobs-search-results-list__list-item"]'
                );
                
                for (const card of cards) {
                    const titleEl = card.querySelector(
                        '[class*="job-card-list__title"], ' +
                        'a[class*="job-card-container__link"],' +
                        'h3, [class*="base-search-card__title"]'
                    );
                    const companyEl = card.querySelector(
                        '[class*="job-card-container__primary-description"], ' +
                        '[class*="base-search-card__subtitle"], ' +
                        '[class*="artdeco-entity-lockup__subtitle"]'
                    );
                    const locationEl = card.querySelector(
                        '[class*="job-card-container__metadata-item"], ' +
                        '[class*="job-search-card__location"]'
                    );
                    const linkEl = card.querySelector('a[href*="/jobs/view/"]');
                    
                    const title = titleEl ? titleEl.innerText.trim() : '';
                    const comp = companyEl ? companyEl.innerText.trim() : company;
                    const location = locationEl ? locationEl.innerText.trim() : '';
                    const href = linkEl ? linkEl.href : '';
                    
                    if (title && title.length > 3) {
                        results.push({
                            title: title.substring(0, 200),
                            url: href,
                            company: comp,
                            location: location,
                            source: 'linkedin_search',
                        });
                    }
                }
                return results;
            }""", company)
            
            for job in extracted:
                title_key = job["title"].lower().strip()
                if title_key not in seen_titles:
                    jobs.append(job)
                    seen_titles.add(title_key)
            
            if extracted:
                break  # Got results, no need for more search terms
                
        except Exception as e:
            if "timeout" not in str(e).lower():
                print(f"      Error: {str(e)[:50]}")
        
        await asyncio.sleep(random.uniform(2, 4))
    
    return jobs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 70)
    print("  LINKEDIN COMPANY JOB SCRAPER — All 500 Companies")
    print("  Using existing LinkedIn session")
    print("=" * 70, flush=True)
    
    # Load companies
    companies_file = OUTPUT_DIR / "500_finance_companies_london_sponsorship.json"
    companies = json.loads(companies_file.read_text())
    print(f"\n  Companies to search: {len(companies)}")
    
    # Load progress
    all_jobs = []
    processed = set()
    if PROGRESS_FILE.exists():
        progress = json.loads(PROGRESS_FILE.read_text())
        all_jobs = progress.get("jobs", [])
        processed = set(progress.get("processed", []))
        print(f"  Resuming: {len(processed)} done, {len(all_jobs)} jobs found")
    
    remaining = [c for c in companies if c["company"] not in processed]
    print(f"  Remaining: {len(remaining)}")
    
    if not remaining:
        print("  All done!")
        return
    
    # Start browser
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
        
        if STORAGE_FILE.exists():
            context_options["storage_state"] = str(STORAGE_FILE)
            print("  Loaded LinkedIn session")
        
        context = await browser.new_context(**context_options)
        page = await context.new_page()
        
        # Verify login
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        if "login" in page.url.lower() or "signin" in page.url.lower():
            print("  ERROR: LinkedIn session expired. Please re-login.")
            await browser.close()
            return
        print("  LinkedIn session valid!\n")
        
        search_terms = ["operations", "analyst", "settlement", "middle office"]
        
        for idx, company_info in enumerate(remaining):
            company = company_info["company"]
            category = company_info["category"]
            
            # Search LinkedIn
            jobs = await search_linkedin_jobs(page, company, search_terms)
            
            # Filter relevant
            relevant = [j for j in jobs if is_relevant(j.get("title", ""))]
            for j in relevant:
                j["category"] = category
            
            if relevant:
                print(f"  [{idx+1:3d}/{len(remaining)}] {company[:40]:<40} | {len(relevant)} relevant")
                for j in relevant[:3]:
                    print(f"    - {j['title'][:55]} ({j.get('company', '')[:25]})")
                if len(relevant) > 3:
                    print(f"    ... +{len(relevant) - 3} more")
                all_jobs.extend(relevant)
            else:
                print(f"  [{idx+1:3d}/{len(remaining)}] {company[:40]:<40} | 0 relevant ({len(jobs)} total)")
            
            processed.add(company)
            
            # Save progress every 20 companies
            if (idx + 1) % 20 == 0:
                PROGRESS_FILE.write_text(json.dumps({
                    "jobs": all_jobs,
                    "processed": list(processed),
                }))
                print(f"\n  --- Progress: {idx+1}/{len(remaining)} | Relevant: {len(all_jobs)} ---\n", flush=True)
            
            # Rate limiting — LinkedIn is sensitive
            await asyncio.sleep(random.uniform(8, 15))
            
            # Longer break every 30 companies
            if (idx + 1) % 30 == 0:
                wait = random.uniform(30, 60)
                print(f"\n  Taking {int(wait)}s break to avoid rate limits...\n", flush=True)
                await asyncio.sleep(wait)
        
        # Final save
        # Deduplicate
        seen = set()
        unique = []
        for j in all_jobs:
            key = f"{j['title'].lower()}-{j.get('company', '').lower()}"
            if key not in seen:
                unique.append(j)
                seen.add(key)
        
        RESULTS_FILE.write_text(json.dumps(unique, indent=2))
        PROGRESS_FILE.write_text(json.dumps({
            "jobs": unique,
            "processed": list(processed),
        }))
        
        print(f"\n{'=' * 70}")
        print(f"  COMPLETE")
        print(f"  Companies searched: {len(processed)}")
        print(f"  Relevant jobs found: {len(unique)}")
        print(f"  Saved: {RESULTS_FILE}")
        print(f"{'=' * 70}")
        
        # Top results
        by_company = {}
        for j in unique:
            by_company.setdefault(j.get("company", ""), []).append(j)
        
        print(f"\n  Top companies:")
        for comp, jobs in sorted(by_company.items(), key=lambda x: -len(x[1]))[:20]:
            print(f"    {comp}: {len(jobs)}")
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
