"""Job scraper — fetch 300+ relevant jobs from LinkedIn, Reed, and company career sites."""

import asyncio
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus, urlencode

from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

from config import STORAGE_STATE, OUTPUT_DIR, DATA_DIR, JOBS_JSON
from humanizer import random_delay

# ============================================================
# SEARCH CONFIGURATION
# ============================================================

# LinkedIn search queries
LINKEDIN_SEARCH_QUERIES = [
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
]

REED_SEARCH_QUERIES = [
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
]

# Direct company career page URLs — these are confirmed visa sponsors
# Each entry: (company_name, career_search_url, css_selectors)
COMPANY_CAREER_PAGES = [
    {
        "company": "Goldman Sachs",
        "url": "https://higher.gs.com/roles?page=1&sortBy=RELEVANCE&location=London&division=OPERATIONS&division=GLOBAL_BANKING_AND_MARKETS",
        "alt_url": "https://www.goldmansachs.com/careers/find-a-job/?search=operations+london",
    },
    {
        "company": "JPMorgan Chase",
        "url": "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions?keyword=operations+analyst&location=London&locationId=300000000289738&locationLevel=city",
        "alt_url": "https://careers.jpmorgan.com/global/en/search?q=operations+analyst&l=London",
    },
    {
        "company": "Citi",
        "url": "https://jobs.citi.com/search-jobs/operations%20analyst/287/1/6252001-2635167-2643743/51.50853/-0.12574/50/2?glat=51.50853&glon=-0.12574",
        "alt_url": "https://jobs.citi.com/search-jobs/trade+operations/287/1?glat=51.50853&glon=-0.12574",
    },
    {
        "company": "Bank of America",
        "url": "https://careers.bankofamerica.com/en-gb/search-results?q=operations+analyst&l=London",
    },
    {
        "company": "Barclays",
        "url": "https://search.jobs.barclays/search-jobs/operations/22545/1/6252001-2635167-2643743/51-50853/-0-12574/50/2",
    },
    {
        "company": "HSBC",
        "url": "https://mycareer.hsbc.com/en_GB/external/SearchJobs/?jobSearch=operations+analyst&location=London",
    },
    {
        "company": "Deutsche Bank",
        "url": "https://careers.db.com/search/?q=operations+analyst&location=London",
    },
    {
        "company": "UBS",
        "url": "https://jobs.ubs.com/TGnewUI/Search/Home/Home?partnerid=25008&siteid=5131#keyWordSearch=operations%20analyst&locationSearch=London",
    },
    {
        "company": "Nomura",
        "url": "https://nomuracareers.com/search/?q=operations+analyst&location=London",
    },
    {
        "company": "BNP Paribas",
        "url": "https://group.bnpparibas/en/careers/job-offers?keyword=operations+analyst&location=London",
    },
    {
        "company": "Societe Generale",
        "url": "https://careers.societegenerale.com/en/search?keyword=operations+analyst&location=London",
    },
    {
        "company": "Standard Chartered",
        "url": "https://scb.taleo.net/careersection/ex/jobsearch.ftl?lang=en&keyword=operations+analyst&location=3100010208",
    },
    {
        "company": "Millennium",
        "url": "https://mlp.wd5.myworkdayjobs.com/en-US/mlpcareers?locationCountry=29a22e734173018a18df8a3d8600032a&q=operations",
    },
    {
        "company": "Citadel",
        "url": "https://www.citadel.com/careers/?location=london&query=operations",
    },
    {
        "company": "Point72",
        "url": "https://careers.point72.com/CSJobSearch?jobSearch=operations&location=London",
    },
    {
        "company": "Balyasny",
        "url": "https://balcareers.com/openings/?search=operations&location=london",
    },
    {
        "company": "Man Group",
        "url": "https://mangroupplc.wd3.myworkdayjobs.com/en-US/ManGroupCareers?locationCountry=e2b8059db2af4b27a3ee999ef5e6c41b&q=operations",
    },
    {
        "company": "Two Sigma",
        "url": "https://careers.twosigma.com/careers/SearchJobs/?2=13901&listFilterMode=1&3_56_3=154",
    },
    {
        "company": "Bridgewater Associates",
        "url": "https://www.bridgewater.com/working-at-bridgewater/job-openings?location=London&department=Investment+Engine",
    },
    {
        "company": "Wellington Management",
        "url": "https://wellington.wd5.myworkdayjobs.com/en-US/External?locationCountry=29247e71b0c64bedb53c4b25c7a18e8e&q=operations",
    },
    {
        "company": "BlackRock",
        "url": "https://careers.blackrock.com/job-search-results/?location=London%2C%20England%2C%20United%20Kingdom&keyword=operations",
    },
    {
        "company": "Schroders",
        "url": "https://schroders.wd3.myworkdayjobs.com/SchrodersCareersSite?locationCountry=e2b8059db2af4b27a3ee999ef5e6c41b&q=operations",
    },
    {
        "company": "Fidelity International",
        "url": "https://fil.wd3.myworkdayjobs.com/faborneexternal?locationCountry=e2b8059db2af4b27a3ee999ef5e6c41b&q=operations",
    },
]

# LinkedIn location
LOCATION = "London"
LOCATION_GEOID = "102257491"  # LinkedIn GeoID for London, UK


# ============================================================
# LINKEDIN SCRAPER
# ============================================================

async def scrape_linkedin_jobs(page: Page, query: str, max_pages: int = 4) -> list[dict]:
    """Search LinkedIn for jobs and collect listings."""
    jobs = []

    for page_num in range(max_pages):
        start = page_num * 25

        params = {
            "keywords": query,
            "location": LOCATION,
            "geoId": LOCATION_GEOID,
            "f_TPR": "r2592000",  # Past month
            "f_E": "2,3,4",  # Entry + Associate + Mid-Senior
            "start": str(start),
            "sortBy": "DD",  # Most recent
        }
        url = f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"

        try:
            await page.goto(url, wait_until="domcontentloaded")
            await random_delay(10, 18)

            # Scroll to load results
            for _ in range(3):
                await page.mouse.wheel(0, random.randint(500, 800))
                await random_delay(2, 4)

            # Try multiple selectors for job cards
            job_cards = await page.locator(
                '.jobs-search-results__list-item, '
                '.job-card-container, '
                'li[data-occludable-job-id]'
            ).all()

            if not job_cards:
                # Alternative: scrape from the job list links
                links = await page.locator(
                    'a[href*="/jobs/view/"]'
                ).all()
                for link in links:
                    try:
                        href = await link.get_attribute("href")
                        text = (await link.inner_text()).strip()
                        if href and text and "/jobs/view/" in href:
                            clean_url = href.split("?")[0]
                            if not clean_url.startswith("http"):
                                clean_url = f"https://www.linkedin.com{clean_url}"
                            jobs.append({
                                "title": text[:100],
                                "company": "",
                                "url": clean_url,
                                "location": "London",
                                "source": "linkedin",
                                "search_query": query,
                            })
                    except Exception:
                        continue
                break

            for card in job_cards:
                try:
                    job = await extract_linkedin_job_card(card)
                    if job:
                        job["source"] = "linkedin"
                        job["search_query"] = query
                        jobs.append(job)
                except Exception:
                    continue

            flush_print(f"    Page {page_num + 1}: {len(job_cards)} cards ({len(jobs)} total for '{query}')")

            # Check if there's a next page
            if len(job_cards) < 20:
                break  # Probably no more results

            await random_delay(12, 25)

        except PlaywrightTimeout:
            flush_print(f"    Timeout on page {page_num + 1} for '{query}'")
            break
        except Exception as e:
            flush_print(f"    Error on page {page_num + 1}: {e}")
            break

    return jobs


async def extract_linkedin_job_card(card) -> dict | None:
    """Extract job data from a LinkedIn job card element."""
    try:
        title = ""
        url = ""
        company = ""

        # Try to get the job link
        link = card.locator('a[href*="/jobs/view/"]').first
        if await link.count() > 0:
            href = await link.get_attribute("href")
            if href:
                url = href.split("?")[0]
                if not url.startswith("http"):
                    url = f"https://www.linkedin.com{url}"
            title = (await link.inner_text()).strip()

        if not url:
            return None

        # Clean title (remove extra whitespace/newlines)
        title = " ".join(title.split())[:150]

        # Get company
        company_selectors = [
            '.job-card-container__primary-description',
            '.artdeco-entity-lockup__subtitle',
            '.job-card-container__company-name',
        ]
        for sel in company_selectors:
            el = card.locator(sel).first
            if await el.count() > 0:
                company = (await el.inner_text()).strip()
                break

        if not company:
            # Try getting text after the title
            all_text = (await card.inner_text()).strip()
            lines = [l.strip() for l in all_text.split("\n") if l.strip()]
            if len(lines) >= 2:
                company = lines[1]  # Usually company is second line

        # Get location
        location = ""
        loc_el = card.locator('.job-card-container__metadata-wrapper, .artdeco-entity-lockup__caption').first
        if await loc_el.count() > 0:
            location = (await loc_el.inner_text()).strip()

        # Easy Apply badge
        easy_apply = "easy apply" in (await card.inner_text()).lower()

        return {
            "title": title,
            "company": company[:100],
            "url": url,
            "location": location[:100],
            "easy_apply": easy_apply,
        }

    except Exception:
        return None


# ============================================================
# REED SCRAPER
# ============================================================

async def scrape_reed_jobs(page: Page, query: str, max_pages: int = 3) -> list[dict]:
    """Search Reed.co.uk for jobs."""
    jobs = []

    for page_num in range(max_pages):
        url = (
            f"https://www.reed.co.uk/jobs/{quote_plus(query)}-jobs-in-london"
            f"?sortby=DisplayDate&proximity=10"
        )
        if page_num > 0:
            url += f"&pageno={page_num + 1}"

        try:
            await page.goto(url, wait_until="domcontentloaded")
            await random_delay(5, 10)

            # Get all job links
            job_links = await page.locator('h2 a[href*="/jobs/"], .job-result-heading__title a').all()

            if not job_links:
                break

            for link in job_links:
                try:
                    title = (await link.inner_text()).strip()
                    href = await link.get_attribute("href")
                    if not href:
                        continue
                    job_url = href if href.startswith("http") else f"https://www.reed.co.uk{href}"

                    # Get company from nearby element
                    parent = link.locator("xpath=ancestor::article | ancestor::div[contains(@class,'job')]").first
                    company = ""
                    if await parent.count() > 0:
                        company_el = parent.locator('.job-result-heading__posted-by a, .gtmJobListingPostedBy, [class*="company"]').first
                        if await company_el.count() > 0:
                            company = (await company_el.inner_text()).strip()

                    jobs.append({
                        "title": title,
                        "company": company,
                        "url": job_url,
                        "location": "London",
                        "source": "reed",
                        "search_query": query,
                    })
                except Exception:
                    continue

            flush_print(f"    Reed page {page_num + 1}: {len(job_links)} links ({len(jobs)} total)")

            if len(job_links) < 20:
                break

            await random_delay(8, 15)

        except Exception as e:
            flush_print(f"    Reed error: {e}")
            break

    return jobs


# ============================================================
# COMPANY CAREER PAGE SCRAPER
# ============================================================

async def scrape_company_careers(page: Page, company_info: dict) -> list[dict]:
    """Scrape jobs from a company's career page. Generic approach."""
    company = company_info["company"]
    url = company_info["url"]
    jobs = []

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await random_delay(5, 10)

        # Scroll to load dynamic content
        for _ in range(3):
            await page.mouse.wheel(0, random.randint(400, 700))
            await random_delay(1, 3)

        # Generic approach: find all links that look like job postings
        # Common patterns: /job/, /role/, /position/, /requisition/, /opening/
        all_links = await page.locator(
            'a[href*="/job"], '
            'a[href*="/role"], '
            'a[href*="/position"], '
            'a[href*="/requisition"], '
            'a[href*="/opening"], '
            'a[href*="jobId"], '
            'a[href*="job-details"], '
            'a[href*="/careers/"]'
        ).all()

        seen_urls = set()
        for link in all_links:
            try:
                href = await link.get_attribute("href")
                if not href or href in seen_urls:
                    continue

                text = (await link.inner_text()).strip()
                if not text or len(text) < 5 or len(text) > 200:
                    continue

                # Skip navigation/header links
                if any(skip in text.lower() for skip in [
                    "back to", "view all", "search", "sign in", "log in",
                    "cookie", "privacy", "terms", "contact",
                ]):
                    continue

                # Build full URL
                if href.startswith("/"):
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    full_url = f"{parsed.scheme}://{parsed.netloc}{href}"
                elif href.startswith("http"):
                    full_url = href
                else:
                    continue

                seen_urls.add(full_url)
                jobs.append({
                    "title": " ".join(text.split())[:150],
                    "company": company,
                    "url": full_url,
                    "location": "London",
                    "source": "career_page",
                    "search_query": f"{company} careers",
                })
            except Exception:
                continue

        # Also try the alt_url if available and we got few results
        if len(jobs) < 5 and "alt_url" in company_info:
            try:
                await page.goto(company_info["alt_url"], wait_until="domcontentloaded", timeout=30000)
                await random_delay(5, 8)

                alt_links = await page.locator('a[href*="/job"], a[href*="/role"], a[href*="/position"]').all()
                for link in alt_links:
                    href = await link.get_attribute("href")
                    text = (await link.inner_text()).strip()
                    if href and text and len(text) > 5 and href not in seen_urls:
                        if href.startswith("/"):
                            from urllib.parse import urlparse
                            parsed = urlparse(company_info["alt_url"])
                            full_url = f"{parsed.scheme}://{parsed.netloc}{href}"
                        elif href.startswith("http"):
                            full_url = href
                        else:
                            continue
                        seen_urls.add(full_url)
                        jobs.append({
                            "title": " ".join(text.split())[:150],
                            "company": company,
                            "url": full_url,
                            "location": "London",
                            "source": "career_page",
                            "search_query": f"{company} careers (alt)",
                        })
            except Exception:
                pass

        flush_print(f"    {company}: {len(jobs)} jobs found")

    except PlaywrightTimeout:
        flush_print(f"    {company}: timeout (page didn't load)")
    except Exception as e:
        flush_print(f"    {company}: error - {str(e)[:80]}")

    return jobs


# ============================================================
# DEDUP & SCORING
# ============================================================

def deduplicate_jobs(new_jobs: list[dict], existing_jobs: list[dict]) -> list[dict]:
    """Remove duplicates based on URL and fuzzy title+company matching."""
    existing_urls = {j["url"].split("?")[0].rstrip("/") for j in existing_jobs}
    existing_keys = {
        (j.get("title", "").lower().strip()[:50], j.get("company", "").lower().strip())
        for j in existing_jobs
    }

    unique = []
    seen_urls = set()

    for job in new_jobs:
        url_clean = job["url"].split("?")[0].rstrip("/")
        key = (job.get("title", "").lower().strip()[:50], job.get("company", "").lower().strip())

        if url_clean in existing_urls or url_clean in seen_urls:
            continue
        if key[0] and key[1] and key in existing_keys:
            continue

        seen_urls.add(url_clean)
        if key[0] and key[1]:
            existing_keys.add(key)
        unique.append(job)

    return unique


def score_relevance(job: dict) -> float:
    """Score a job's relevance to Nidhi's profile (0-100)."""
    title = job.get("title", "").lower()
    company = job.get("company", "").lower()
    score = 0

    # High-value title keywords (Nidhi's core skills)
    high_keywords = [
        "trade support", "trade operations", "middle office",
        "settlement", "prime brokerage", "operations analyst",
        "trade processing", "post trade", "reconciliation",
    ]
    for kw in high_keywords:
        if kw in title:
            score += 20

    # Medium-value keywords
    med_keywords = [
        "fixed income", "equity", "derivatives", "securities",
        "fund operations", "investment operations", "clearing",
        "collateral", "treasury", "markets operations",
        "trade control", "trade lifecycle", "matching",
    ]
    for kw in med_keywords:
        if kw in title:
            score += 10

    # Role level indicators
    if any(w in title for w in ["analyst", "associate", "specialist"]):
        score += 10
    if "senior" in title or "avp" in title:
        score += 5
    if "vp" in title and "avp" not in title:
        score += 0  # Neutral — VP might be too senior or might be fine
    if "director" in title or "head of" in title or "managing" in title:
        score -= 20
    if "intern" in title or "graduate" in title:
        score -= 10

    # Company type bonus (confirmed visa sponsors)
    tier1_banks = [
        "goldman", "jpmorgan", "jp morgan", "citi", "barclays",
        "hsbc", "morgan stanley", "bank of america", "deutsche",
        "ubs", "nomura", "societe generale", "bnp paribas",
        "standard chartered", "rbc", "td securities", "icbc",
    ]
    top_hfs = [
        "millennium", "citadel", "balyasny", "point72", "two sigma",
        "bridgewater", "man group", "winton", "qube", "capstone",
        "ares", "apollo", "blackstone", "kkr", "carlyle",
        "wellington", "fidelity", "blackrock", "schroders",
    ]
    other_finance = [
        "aviva", "barings", "quilter", "bloomberg", "deloitte",
        "lme", "lseg", "euroclear", "ice", "cme",
    ]

    for kw in tier1_banks:
        if kw in company:
            score += 15
            break
    else:
        for kw in top_hfs:
            if kw in company:
                score += 12
                break
        else:
            for kw in other_finance:
                if kw in company:
                    score += 8
                    break

    # Penalize irrelevant roles
    irrelevant = [
        "software engineer", "developer", "devops", "data engineer",
        "sales", "marketing", "hr ", "human resources", "legal",
        "compliance officer", "receptionist", "admin assistant",
    ]
    for kw in irrelevant:
        if kw in title:
            score -= 30

    # Bonus for London-specific
    location = job.get("location", "").lower()
    if "london" in location:
        score += 5

    return max(0, min(100, score))


# ============================================================
# UTILITY
# ============================================================

def flush_print(msg: str):
    """Print with immediate flush (for background processes)."""
    print(msg, flush=True)


# ============================================================
# MAIN
# ============================================================

async def main():
    """Main scraping loop."""
    flush_print("=" * 60)
    flush_print("  JOB SCRAPER — Finding 300+ relevant jobs for Nidhi")
    flush_print("=" * 60)

    # Load existing jobs to avoid duplicates
    existing_jobs = []
    if JOBS_JSON.exists():
        with open(JOBS_JSON) as f:
            existing_jobs = json.load(f)
    flush_print(f"\n  Existing jobs: {len(existing_jobs)}")

    all_new_jobs = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        context_options = {
            "viewport": {"width": 1366, "height": 768},
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "locale": "en-GB",
            "timezone_id": "Europe/London",
        }

        # Load saved session
        if STORAGE_STATE.exists():
            context_options["storage_state"] = str(STORAGE_STATE)
            flush_print("  Loaded saved LinkedIn session")
        else:
            flush_print("  WARNING: No saved session — LinkedIn scraping may fail")

        context = await browser.new_context(**context_options)
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = await context.new_page()

        # Verify LinkedIn login
        flush_print("\n  Verifying LinkedIn session...")
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        await random_delay(8, 12)

        linkedin_logged_in = "/login" not in page.url and "/authwall" not in page.url
        if linkedin_logged_in:
            flush_print("  LinkedIn session: ACTIVE")
        else:
            flush_print("  LinkedIn session: EXPIRED — will still scrape Reed + career pages")

        # ==========================================
        # PHASE 1: Company Career Pages (no login needed, fastest)
        # ==========================================
        flush_print(f"\n{'='*60}")
        flush_print(f"  PHASE 1: Scraping {len(COMPANY_CAREER_PAGES)} company career pages")
        flush_print(f"{'='*60}\n")

        for idx, company_info in enumerate(COMPANY_CAREER_PAGES):
            flush_print(f"  [{idx+1}/{len(COMPANY_CAREER_PAGES)}] {company_info['company']}")
            jobs = await scrape_company_careers(page, company_info)
            all_new_jobs.extend(jobs)
            await random_delay(5, 12)

        flush_print(f"\n  Phase 1 total: {len(all_new_jobs)} jobs from career pages")

        # ==========================================
        # PHASE 2: Reed.co.uk (no login needed)
        # ==========================================
        flush_print(f"\n{'='*60}")
        flush_print(f"  PHASE 2: Searching Reed.co.uk ({len(REED_SEARCH_QUERIES)} queries)")
        flush_print(f"{'='*60}\n")

        for idx, query in enumerate(REED_SEARCH_QUERIES):
            flush_print(f"  [{idx+1}/{len(REED_SEARCH_QUERIES)}] Reed: '{query}'")
            jobs = await scrape_reed_jobs(page, query, max_pages=3)
            all_new_jobs.extend(jobs)
            flush_print(f"    -> {len(jobs)} jobs")
            await random_delay(8, 15)

        flush_print(f"\n  Running total: {len(all_new_jobs)} jobs")

        # ==========================================
        # PHASE 3: LinkedIn (needs session, longest delays)
        # ==========================================
        if linkedin_logged_in:
            flush_print(f"\n{'='*60}")
            flush_print(f"  PHASE 3: Searching LinkedIn ({len(LINKEDIN_SEARCH_QUERIES)} queries)")
            flush_print(f"  Delays: 15-30s between searches, 60-120s break every 8 queries")
            flush_print(f"{'='*60}\n")

            for idx, query in enumerate(LINKEDIN_SEARCH_QUERIES):
                flush_print(f"  [{idx+1}/{len(LINKEDIN_SEARCH_QUERIES)}] LinkedIn: '{query}'")
                jobs = await scrape_linkedin_jobs(page, query, max_pages=3)
                all_new_jobs.extend(jobs)
                flush_print(f"    -> {len(jobs)} jobs (total: {len(all_new_jobs)})")

                # Long delays between LinkedIn searches
                delay = random.uniform(15, 30)
                flush_print(f"    Waiting {delay:.0f}s...")
                await asyncio.sleep(delay)

                # Every 8 queries, take a longer break
                if (idx + 1) % 8 == 0 and idx < len(LINKEDIN_SEARCH_QUERIES) - 1:
                    long_delay = random.uniform(60, 120)
                    flush_print(f"\n  --- Taking {long_delay:.0f}s break (anti-detection) ---\n")
                    await asyncio.sleep(long_delay)
        else:
            flush_print("\n  SKIPPING LinkedIn (not logged in)")

        # Save session
        await context.storage_state(path=str(STORAGE_STATE))
        await browser.close()

    # ==========================================
    # POST-PROCESSING
    # ==========================================
    flush_print(f"\n{'='*60}")
    flush_print(f"  POST-PROCESSING")
    flush_print(f"{'='*60}")
    flush_print(f"\n  Raw results: {len(all_new_jobs)} jobs collected")

    # Deduplicate
    unique_jobs = deduplicate_jobs(all_new_jobs, existing_jobs)
    flush_print(f"  After dedup: {len(unique_jobs)} unique new jobs")

    # Score relevance
    for job in unique_jobs:
        job["relevance_score"] = score_relevance(job)

    # Sort by relevance (highest first)
    unique_jobs.sort(key=lambda j: j["relevance_score"], reverse=True)

    # Take top 300
    top_jobs = unique_jobs[:300]
    if top_jobs:
        flush_print(f"  Top 300: min score = {top_jobs[-1]['relevance_score']}, max = {top_jobs[0]['relevance_score']}")
    else:
        flush_print("  WARNING: No jobs collected!")

    # Assign IDs
    max_existing_id = max((j.get("id", 0) for j in existing_jobs), default=0)
    for idx, job in enumerate(top_jobs):
        job["id"] = max_existing_id + idx + 1

    # Save results
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DATA_DIR / "jobs_new_300.json"
    with open(output_path, "w") as f:
        json.dump(top_jobs, f, indent=2)
    flush_print(f"\n  Saved: {output_path} ({len(top_jobs)} jobs)")

    # Combined list
    combined = existing_jobs + top_jobs
    combined_path = DATA_DIR / "jobs_all.json"
    with open(combined_path, "w") as f:
        json.dump(combined, f, indent=2)
    flush_print(f"  Saved: {combined_path} ({len(combined)} total)")

    # Also save as markdown for readability
    md_path = DATA_DIR / "jobs_new_300.md"
    with open(md_path, "w") as f:
        f.write(f"# New Jobs — Scraped {time.strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(f"Total: {len(top_jobs)} jobs (from {len(all_new_jobs)} raw, {len(unique_jobs)} unique)\n\n")
        f.write("| # | Score | Title | Company | Source | URL |\n")
        f.write("|---|-------|-------|---------|--------|-----|\n")
        for job in top_jobs:
            f.write(
                f"| {job['id']} | {job['relevance_score']:.0f} | "
                f"{job['title'][:60]} | {job.get('company', '?')[:30]} | "
                f"{job.get('source', '?')} | {job['url'][:80]} |\n"
            )
    flush_print(f"  Saved: {md_path}")

    # Print summary
    flush_print(f"\n{'='*60}")
    flush_print(f"  RESULTS SUMMARY")
    flush_print(f"{'='*60}")

    # Source breakdown
    sources = {}
    for j in top_jobs:
        src = j.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    flush_print(f"\n  By source:")
    for src, count in sorted(sources.items(), key=lambda x: -x[1]):
        flush_print(f"    {src}: {count}")

    # Top 20 preview
    flush_print(f"\n  TOP 20 MOST RELEVANT:")
    for job in top_jobs[:20]:
        flush_print(f"    [{job['relevance_score']:3.0f}] {job['title'][:55]}")
        flush_print(f"         {job.get('company', '?')[:35]} | {job.get('source', '?')}")

    flush_print(f"\n  Done! {len(top_jobs)} new jobs ready for application.")


if __name__ == "__main__":
    asyncio.run(main())
