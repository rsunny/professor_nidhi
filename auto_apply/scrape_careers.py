"""Careers Scraper FINAL — Uses public LinkedIn Jobs + Workday/Greenhouse APIs.

Strategy:
1. Workday/Greenhouse/Lever APIs (direct, fastest) — ~75 companies
2. Public LinkedIn Jobs search (no login needed!) — ALL 500 companies

Public LinkedIn shows ~25 results per company search without login.
With 500 companies, we should find hundreds of relevant jobs.

Usage:
    python3 -u scrape_careers.py
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
import httpx
from playwright.async_api import async_playwright, Page

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "output"
RESULTS_FILE = DATA_DIR / "careers_scrape_results.json"
PROGRESS_FILE = DATA_DIR / "careers_scrape_progress.json"

# ---------------------------------------------------------------------------
# Relevance Filter
# ---------------------------------------------------------------------------

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
    r"\bintern\b", r"\bapprentice\b", r"\bprincipal\b",
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
# Workday API scraper
# ---------------------------------------------------------------------------

WORKDAY_SITES = {
    "Citigroup": ("citi", "wd5", "2"),
    "Aviva": ("aviva", "wd1", "External"),
    "PIMCO Europe": ("pimco", "wd1", "pimco-careers"),
    "Janus Henderson Investors": ("janushenderson", "wd1", "JHIExternalCareerSite"),
    "Columbia Threadneedle": ("columbiathreadneedle", "wd1", "ColumbiathreadneedleCareers"),
    "Invesco": ("invesco", "wd1", "Invesco"),
    "Wellington Management": ("wellington", "wd5", "Wellington_Careers"),
    "Franklin Templeton": ("franklintempleton", "wd5", "FTI_External_Career_Site"),
    "Fidelity International": ("fil", "wd3", "faborjobs"),
    "M&G Investments": ("mandg", "wd3", "M_and_G_Careers"),
    "abrdn (Aberdeen)": ("abrdn", "wd3", "abrdn_Careers"),
    "Ninety One": ("ninetyone", "wd3", "NinetyOneCareers"),
    "State Street": ("statestreet", "wd1", "Global"),
    "Apex Group": ("theapexgroup", "wd3", "ApexGroupCareers"),
    "Marsh McLennan": ("mmc", "wd1", "Careers"),
    "Aon": ("aon", "wd1", "Careers"),
    "CME Group": ("cmegroup", "wd1", "CME_Group_Careers"),
    "DTCC": ("dtcc", "wd1", "DTCC_Careers_External"),
    "FIS (Fidelity National Info Services)": ("fisglobal", "wd1", "FIS_Careers"),
    "Finastra": ("finastra", "wd3", "Finastra"),
    "NatWest Markets": ("natwestgroup", "wd3", "natwestgroup"),
}


async def scrape_workday_api(client: httpx.AsyncClient, company: str, subdomain: str, 
                             wd_num: str, site: str) -> list[dict]:
    """Hit Workday API directly."""
    jobs = []
    base_url = f"https://{subdomain}.{wd_num}.myworkdayjobs.com/wday/cxs/{subdomain}/{site}/jobs"
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    
    for search_text in ["operations London", "analyst London", "settlement London", "finance London"]:
        payload = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": search_text}
        try:
            resp = await client.post(base_url, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                for job in data.get("jobPostings", []):
                    loc = job.get("locationsText", "").lower()
                    if "london" in loc or "united kingdom" in loc:
                        ext_path = job.get("externalPath", "")
                        url = f"https://{subdomain}.{wd_num}.myworkdayjobs.com/en-US/{site}{ext_path}" if ext_path else ""
                        jobs.append({
                            "title": job.get("title", ""),
                            "url": url,
                            "company": company,
                            "location": job.get("locationsText", ""),
                            "source": "workday_api",
                        })
                # Paginate if needed
                total = data.get("total", 0)
                if total > 20:
                    for offset in range(20, min(total, 80), 20):
                        payload["offset"] = offset
                        try:
                            r2 = await client.post(base_url, json=payload, headers=headers)
                            if r2.status_code == 200:
                                for job in r2.json().get("jobPostings", []):
                                    loc = job.get("locationsText", "").lower()
                                    if "london" in loc or "united kingdom" in loc:
                                        ext_path = job.get("externalPath", "")
                                        url = f"https://{subdomain}.{wd_num}.myworkdayjobs.com/en-US/{site}{ext_path}" if ext_path else ""
                                        jobs.append({"title": job.get("title", ""), "url": url, 
                                                    "company": company, "location": job.get("locationsText", ""), "source": "workday_api"})
                        except Exception:
                            pass
                if jobs:
                    break
        except Exception:
            continue
    return jobs


# ---------------------------------------------------------------------------
# Greenhouse API
# ---------------------------------------------------------------------------

GREENHOUSE_COMPANIES = {
    "Revolut": "revolut", "Checkout.com": "checkout", "Monzo Bank": "monzo",
    "Starling Bank": "starling-bank", "OakNorth Bank": "oaknorth-bank",
    "GoCardless": "gocardless", "Thought Machine": "thought-machine-ltd",
    "Citadel Europe": "citadel", "Two Sigma International": "twosigma",
    "Point72 Europe": "point72", "D.E. Shaw": "d-e-shaw",
    "Jane Street": "janestreet", "Man Group": "mangroup",
    "Brevan Howard": "brevanhoward", "Marshall Wace": "marshall-wace",
    "TP ICAP": "tpicap", "Bloomberg LP": "bloomberg-lp",
    "Funding Circle": "funding-circle", "WorldRemit": "worldremit",
    "XTX Markets": "xtx-markets", "Flow Traders": "flowtraders",
    "CLS Group": "cls-group", "MarketAxess": "marketaxess",
    "10x Banking": "10xbanking", "Wise (TransferWise)": "transferwise",
}


async def scrape_greenhouse_api(client: httpx.AsyncClient, company: str, board: str) -> list[dict]:
    """Hit Greenhouse API."""
    jobs = []
    try:
        resp = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true", timeout=15)
        if resp.status_code == 200:
            for job in resp.json().get("jobs", []):
                loc = job.get("location", {}).get("name", "") if job.get("location") else ""
                if "london" in loc.lower() or "uk" in loc.lower() or not loc:
                    jobs.append({
                        "title": job.get("title", ""),
                        "url": job.get("absolute_url", ""),
                        "company": company,
                        "location": loc,
                        "source": "greenhouse_api",
                    })
    except Exception:
        pass
    return jobs


# ---------------------------------------------------------------------------
# Public LinkedIn Jobs Search (NO LOGIN NEEDED)
# ---------------------------------------------------------------------------

async def search_linkedin_public(page: Page, company: str) -> list[dict]:
    """Search public LinkedIn jobs for a company."""
    jobs = []
    
    # Search with company name + operations/analyst in London
    query = f"{company} operations"
    url = (
        f"https://www.linkedin.com/jobs/search/"
        f"?keywords={quote_plus(query)}"
        f"&location=London%2C%20United%20Kingdom"
        f"&f_JT=F"  # Full-time only
        f"&position=1&pageNum=0"
    )
    
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(random.uniform(3, 5))
        
        # Scroll to load more
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)
        
        # Extract job cards (public view has different selectors)
        extracted = await page.evaluate("""(company) => {
            const results = [];
            const seen = new Set();
            const cards = document.querySelectorAll(
                '.base-card, .job-search-card, [class*="base-search-card"], ' +
                '[class*="job-card"], li[class*="result"]'
            );
            
            for (const card of cards) {
                const titleEl = card.querySelector(
                    'h3, [class*="title"], [class*="base-search-card__title"]'
                );
                const compEl = card.querySelector(
                    'h4, [class*="subtitle"], [class*="base-search-card__subtitle"]'
                );
                const locEl = card.querySelector(
                    '[class*="location"], [class*="job-search-card__location"]'
                );
                const linkEl = card.querySelector('a[href*="/jobs/"]');
                
                const title = titleEl ? titleEl.innerText.trim() : '';
                const comp = compEl ? compEl.innerText.trim() : '';
                const location = locEl ? locEl.innerText.trim() : '';
                const href = linkEl ? linkEl.href.split('?')[0] : '';
                
                if (title && title.length > 3 && !seen.has(title.toLowerCase())) {
                    seen.add(title.toLowerCase());
                    results.push({
                        title: title.substring(0, 200),
                        url: href,
                        company: comp || company,
                        location: location,
                        source: 'linkedin_public',
                    });
                }
            }
            return results;
        }""", company)
        
        jobs = extracted
        
    except Exception as e:
        if "timeout" not in str(e).lower():
            pass
    
    return jobs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 70)
    print("  CAREERS SCRAPER — ALL 500 Companies")
    print("  Phase 1: Workday/Greenhouse APIs (fast)")
    print("  Phase 2: Public LinkedIn Jobs (no login, all companies)")
    print("=" * 70, flush=True)
    
    # Load companies
    companies_file = OUTPUT_DIR / "500_finance_companies_london_sponsorship.json"
    companies = json.loads(companies_file.read_text())
    print(f"\n  Total companies: {len(companies)}")
    
    # Load progress
    all_jobs = []
    processed = set()
    if PROGRESS_FILE.exists():
        try:
            progress = json.loads(PROGRESS_FILE.read_text())
            if progress.get("version") == "v4":
                all_jobs = progress.get("jobs", [])
                processed = set(progress.get("processed", []))
                print(f"  Resuming: {len(processed)} done, {len(all_jobs)} relevant jobs")
        except Exception:
            pass
    
    remaining = [c for c in companies if c["company"] not in processed]
    print(f"  Remaining: {len(remaining)}\n")
    
    # ==========================================
    # PHASE 1: APIs
    # ==========================================
    api_companies = [c for c in remaining if c["company"] in WORKDAY_SITES or c["company"] in GREENHOUSE_COMPANIES]
    
    if api_companies:
        print(f"  PHASE 1: {len(api_companies)} companies via API...")
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            for c in api_companies:
                company = c["company"]
                category = c["category"]
                jobs = []
                
                if company in WORKDAY_SITES:
                    sub, wd, site = WORKDAY_SITES[company]
                    jobs = await scrape_workday_api(client, company, sub, wd, site)
                elif company in GREENHOUSE_COMPANIES:
                    board = GREENHOUSE_COMPANIES[company]
                    jobs = await scrape_greenhouse_api(client, company, board)
                
                relevant = [j for j in jobs if is_relevant(j["title"])]
                for j in relevant:
                    j["category"] = category
                
                if relevant:
                    print(f"    [{company[:35]:<35}] {len(relevant)} relevant")
                    for j in relevant[:2]:
                        print(f"      - {j['title'][:55]}")
                
                all_jobs.extend(relevant)
                processed.add(company)
        
        PROGRESS_FILE.write_text(json.dumps({"version": "v4", "jobs": all_jobs, "processed": list(processed)}))
        print(f"\n  Phase 1 done: {len(all_jobs)} relevant jobs\n")
    
    # ==========================================
    # PHASE 2: Public LinkedIn (ALL remaining)
    # ==========================================
    linkedin_remaining = [c for c in companies if c["company"] not in processed]
    
    if linkedin_remaining:
        print(f"  PHASE 2: {len(linkedin_remaining)} companies via public LinkedIn...\n")
        
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="en-GB",
            )
            page = await context.new_page()
            
            for idx, c in enumerate(linkedin_remaining):
                company = c["company"]
                category = c["category"]
                
                jobs = await search_linkedin_public(page, company)
                relevant = [j for j in jobs if is_relevant(j["title"])]
                for j in relevant:
                    j["category"] = category
                
                if relevant:
                    print(f"  [{idx+1:3d}/{len(linkedin_remaining)}] {company[:35]:<35} | {len(relevant)} relevant (of {len(jobs)})")
                    for j in relevant[:2]:
                        print(f"    - {j['title'][:55]} | {j.get('company','')[:20]}")
                    if len(relevant) > 2:
                        print(f"    ... +{len(relevant) - 2} more")
                    all_jobs.extend(relevant)
                else:
                    print(f"  [{idx+1:3d}/{len(linkedin_remaining)}] {company[:35]:<35} | 0 relevant ({len(jobs)} total)")
                
                processed.add(company)
                
                # Save every 25
                if (idx + 1) % 25 == 0:
                    PROGRESS_FILE.write_text(json.dumps({"version": "v4", "jobs": all_jobs, "processed": list(processed)}))
                    print(f"\n  --- Progress: {idx+1}/{len(linkedin_remaining)} | Relevant: {len(all_jobs)} ---\n", flush=True)
                
                # Rate limit: 5-10s between searches
                await asyncio.sleep(random.uniform(5, 10))
                
                # Longer break every 40
                if (idx + 1) % 40 == 0:
                    wait = random.uniform(30, 50)
                    print(f"  Break: {int(wait)}s...", flush=True)
                    await asyncio.sleep(wait)
            
            await browser.close()
    
    # ==========================================
    # FINAL: Deduplicate and save
    # ==========================================
    seen = set()
    unique = []
    for j in all_jobs:
        key = f"{j['title'].lower().strip()}-{j.get('company', '').lower().strip()}"
        if key not in seen:
            unique.append(j)
            seen.add(key)
    
    RESULTS_FILE.write_text(json.dumps(unique, indent=2))
    PROGRESS_FILE.write_text(json.dumps({"version": "v4", "jobs": unique, "processed": list(processed)}))
    
    # Save readable
    md_lines = ["# Relevant Jobs for Nidhi — Scraped from 500 Companies\n"]
    md_lines.append(f"**{len(unique)} relevant jobs found across {len(processed)} companies**\n---\n")
    by_company = {}
    for j in unique:
        by_company.setdefault(j.get("company", ""), []).append(j)
    for comp in sorted(by_company.keys()):
        jobs = by_company[comp]
        md_lines.append(f"\n## {comp} ({len(jobs)})\n")
        for j in jobs:
            url = j.get("url", "")
            if url:
                md_lines.append(f"- [{j['title']}]({url}) — {j.get('location', '')}")
            else:
                md_lines.append(f"- {j['title']} — {j.get('location', '')}")
    (DATA_DIR / "careers_scrape_results.md").write_text("\n".join(md_lines))
    
    print(f"\n{'=' * 70}")
    print(f"  COMPLETE")
    print(f"  Companies: {len(processed)}/{len(companies)}")
    print(f"  Relevant jobs: {len(unique)}")
    print(f"  Saved: {RESULTS_FILE}")
    print(f"{'=' * 70}")
    if by_company:
        print(f"\n  Top companies:")
        for comp, jobs in sorted(by_company.items(), key=lambda x: -len(x[1]))[:25]:
            print(f"    {comp}: {len(jobs)}")


if __name__ == "__main__":
    asyncio.run(main())
