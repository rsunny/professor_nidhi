"""AI-agent-driven job scraper — uses Claude Haiku to read pages and extract rich job data.

Saves intermediate progress so the pipeline can be stopped and resumed:
  - data/jobs_discovered.json  → saved after Stage 1 (discovery)
  - data/jobs_enriched.json    → updated every 10 jobs during Stage 2
  - data/jobs_scraped.json     → final filtered & scored output
"""

import asyncio
import json
import os
import random
import re
import time
from pathlib import Path
from urllib.parse import urlencode, quote_plus

from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

from config import STORAGE_STATE, OUTPUT_DIR, DATA_DIR, JOBS_JSON
from humanizer import random_delay
from ai_navigator import (
    get_client,
    get_page_snapshot,
    get_interactive_elements,
    click_element_by_index,
    dismiss_overlays,
    parse_action,
)

# Intermediate file paths
DISCOVERED_JSON = DATA_DIR / "jobs_discovered.json"
ENRICHED_JSON = DATA_DIR / "jobs_enriched.json"

# ============================================================
# SEARCH CONFIGURATION
# ============================================================

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

LOCATION = "London"
LOCATION_GEOID = "102257491"

# Filtering thresholds
MIN_SALARY = 60000


# ============================================================
# STAGE 1: DISCOVER — AI scrapes search results
# ============================================================

def build_scrape_system_prompt(source: str, query: str) -> str:
    """System prompt for the search results scraping agent."""
    return f"""You are a browser automation agent scraping job listings from search results.

SOURCE: {source}
SEARCH QUERY: "{query}"

Your job is to look at the current page and either:
1. Extract all visible job listings into structured data
2. Navigate (scroll, click pagination) to find more jobs
3. Signal when done

AVAILABLE ACTIONS (respond with ONE JSON per turn):

- Extract jobs visible on the page:
  {{"type": "EXTRACT_JOBS", "jobs": [
    {{"title": "Job Title", "company": "Company Name", "url": "https://full-url-to-job", "salary": "salary if shown or empty string", "location": "location if shown"}}
  ]}}

- Click a pagination/load-more button:
  {{"type": "CLICK", "index": <element_index>, "description": "what you're clicking (e.g. Next page, Load more)"}}

- Scroll down to reveal more listings:
  {{"type": "SCROLL", "description": "scrolling to see more results"}}

- Done — no more jobs or pages to scrape:
  {{"type": "DONE", "reason": "why stopping (e.g. no more results, last page reached)"}}

CRITICAL URL RULES:
- The "url" field MUST be the actual clickable link to the individual job posting page.
- Look for the href attribute on the job title link — that is the real URL.
- Do NOT fabricate or guess URLs. If you cannot find a real link, set url to "".
- URLs must be absolute (start with http/https). If you see a relative path like "/job/12345", prefix it with the site domain (e.g. "https://higher.gs.com/job/12345").
- NEVER use descriptive slugs as URLs (e.g. "/job/software-engineering-hyderabad" is NOT a real job URL — it's a category page). Only use URLs that contain a numeric job ID or a specific job-posting path.
- If the page shows a list of job cards without individual links (just titles), you need to CLICK on each card to get the URL, OR report the jobs with url="" so they can be skipped.

OTHER RULES:
- Respond with ONLY valid JSON — no extra text
- ONE action per turn
- When you see job listings, ALWAYS use EXTRACT_JOBS first before navigating to next page
- For each job, extract: title, company, url, salary (if visible), location
- If salary is not shown on the search results page, leave it as ""
- After extracting jobs from current page, click Next/Show More if available
- If there are no results or the page shows an error, use DONE immediately
- Do NOT extract the same jobs twice — if you've already extracted them, move to next page or DONE
- Only extract jobs in LONDON or UK unless location is unclear.
"""


async def ai_scrape_search_results(
    page: Page, url: str, source: str, query: str, max_pages: int = 4
) -> list[dict]:
    """Use AI agent to scrape job listings from any search results page."""
    client = get_client()
    jobs = []
    max_turns = max_pages * 5

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await random_delay(5, 10)
    except PlaywrightTimeout:
        flush_print(f"    Timeout loading: {url[:80]}")
        return jobs
    except Exception as e:
        flush_print(f"    Error loading page: {str(e)[:80]}")
        return jobs

    await dismiss_overlays(page)
    await random_delay(2, 4)

    # Scroll to trigger lazy-loading
    for _ in range(2):
        await page.mouse.wheel(0, random.randint(400, 700))
        await random_delay(1, 2)

    system_prompt = build_scrape_system_prompt(source, query)
    messages = []

    for turn in range(max_turns):
        snapshot = await get_page_snapshot(page)
        messages.append({
            "role": "user",
            "content": f"Turn {turn + 1}. Current page state:\n\n{snapshot}\n\nWhat action should I take?"
        })

        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1"),
            max_tokens=4000,
            system=system_prompt,
            messages=messages,
        )

        assistant_msg = response.content[0].text.strip()
        messages.append({"role": "assistant", "content": assistant_msg})

        action = _parse_scrape_action(assistant_msg)
        if not action:
            flush_print(f"    [ai] Turn {turn+1}: Could not parse response")
            continue

        action_type = action.get("type")

        if action_type == "EXTRACT_JOBS":
            extracted = action.get("jobs", [])
            for job_data in extracted:
                if job_data.get("url") and job_data.get("title"):
                    jobs.append({
                        "title": job_data.get("title", "")[:150],
                        "company": job_data.get("company", "")[:100],
                        "url": job_data["url"],
                        "salary": job_data.get("salary", ""),
                        "location": job_data.get("location", "London"),
                        "source": source,
                        "search_query": query,
                    })
            flush_print(f"    [ai] Extracted {len(extracted)} jobs (total: {len(jobs)})")

        elif action_type == "CLICK":
            idx = action.get("index")
            desc = action.get("description", "")
            flush_print(f"    [ai] Click [{idx}]: {desc}")
            success = await click_element_by_index(page, idx)
            if success:
                await random_delay(5, 10)
                await dismiss_overlays(page)
            else:
                messages.append({
                    "role": "user",
                    "content": "That element could not be clicked. Try a different element or use DONE if there are no more pages."
                })

        elif action_type == "SCROLL":
            await page.mouse.wheel(0, random.randint(600, 1000))
            await random_delay(2, 4)

        elif action_type == "DONE":
            reason = action.get("reason", "")
            flush_print(f"    [ai] Done: {reason}")
            break

        else:
            flush_print(f"    [ai] Unknown action: {action_type}")

    return jobs


def _parse_scrape_action(response: str) -> dict | None:
    """Parse action from AI response. Handles EXTRACT_JOBS with nested JSON arrays."""
    response = response.strip()

    if response.startswith("{"):
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

    # Find outermost JSON object (handles nested arrays/objects)
    brace_depth = 0
    start = -1
    for i, ch in enumerate(response):
        if ch == "{":
            if brace_depth == 0:
                start = i
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0 and start >= 0:
                try:
                    return json.loads(response[start:i + 1])
                except json.JSONDecodeError:
                    pass
                start = -1

    return parse_action(response)


# ============================================================
# STAGE 2: ENRICH — AI opens each job page and extracts full details
# ============================================================

ENRICH_SYSTEM_PROMPT = """You are a browser automation agent extracting detailed information from a job posting page.

Your task: Read the entire job page and extract ALL available information into a structured format.

AVAILABLE ACTIONS (respond with ONE JSON per turn):

- Job details found — extract everything visible:
  {"type": "JOB_DETAILS", "data": {
    "description": "Full job description text (responsibilities, what the role involves). Include key bullet points.",
    "salary": "Exact salary/compensation as shown (e.g. '£65,000 - £80,000 + bonus', 'Competitive'). Empty string if not shown.",
    "location": "Office location (e.g. 'London, Canary Wharf')",
    "work_mode": "office/hybrid/remote/not_specified",
    "experience_years": "Required experience (e.g. '3-5 years', '2+ years'). Empty if not specified.",
    "seniority": "junior/mid/senior/vp/director/not_specified",
    "skills_required": ["skill1", "skill2", "skill3"],
    "qualifications": ["qualification1", "qualification2"],
    "visa_sponsorship": "yes/no/not_mentioned",
    "contract_type": "permanent/contract/ftc/temp/not_specified",
    "application_type": "easy_apply/external_form/email/not_clear",
    "posted_date": "When posted (e.g. '2 days ago', '15 June 2025'). Empty if not shown.",
    "deadline": "Application deadline if shown. Empty if not shown.",
    "benefits": "Key benefits mentioned (e.g. 'pension, private healthcare, 25 days holiday')",
    "team_department": "Which team/department (e.g. 'Equity Derivatives Operations', 'Global Markets')",
    "is_finance_role": true/false,
    "requires_office": true/false
  }}

- Scroll down to see more of the job description:
  {"type": "SCROLL", "description": "scrolling to see full description"}

- Click to expand a collapsed section (e.g. "Show more", "See full description"):
  {"type": "CLICK", "index": <element_index>, "description": "expanding section"}

- Page failed to load or job is expired/removed:
  {"type": "FAILED", "reason": "why (e.g. 'job expired', 'page not found', '404 error')"}

RULES:
- Respond with ONLY valid JSON — no extra text
- ONE action per turn
- On first look, if you can see enough info, use JOB_DETAILS immediately
- If the description is cut off with "Show more" or "See more", CLICK to expand FIRST, then extract
- For "description": include the FULL text — responsibilities, requirements, what you'll do. Aim for 200-500 words. Include bullet points.
- For "skills_required": extract specific technical/domain skills (e.g. "Trade Settlement", "Bloomberg", "Excel", "Python", "SQL", "Fixed Income")
- For "is_finance_role": true if the role is primarily in finance/banking/investment operations. false if it's pure tech/engineering/marketing/HR.
- For "requires_office": true if they mention office-based, in-office, or specific office location without remote option. false if fully remote. true if hybrid (they still need to go in).
- For "visa_sponsorship": "yes" if they mention sponsoring visas, "no" if they explicitly say no sponsorship, "not_mentioned" otherwise.
- Maximum 5 turns — extract what you can see.
"""


async def ai_enrich_job(page: Page, job: dict) -> dict:
    """Open a job page and use AI to extract full details.

    Returns the enriched job dict with all additional fields.
    """
    client = get_client()
    job_url = job["url"]

    try:
        await page.goto(job_url, wait_until="domcontentloaded", timeout=20000)
        await random_delay(3, 6)
    except PlaywrightTimeout:
        job["enrichment_status"] = "timeout"
        return job
    except Exception as e:
        job["enrichment_status"] = f"error: {str(e)[:50]}"
        return job

    await dismiss_overlays(page)

    messages = []
    max_turns = 5

    for turn in range(max_turns):
        snapshot = await get_page_snapshot(page)
        messages.append({
            "role": "user",
            "content": f"Turn {turn + 1}. Extract all job details from this page:\n\n{snapshot}"
        })

        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1"),
            max_tokens=2000,
            system=ENRICH_SYSTEM_PROMPT,
            messages=messages,
        )

        assistant_msg = response.content[0].text.strip()
        messages.append({"role": "assistant", "content": assistant_msg})

        action = _parse_scrape_action(assistant_msg)
        if not action:
            continue

        action_type = action.get("type")

        if action_type == "JOB_DETAILS":
            data = action.get("data", {})
            # Merge enriched data into job dict
            job["description"] = data.get("description", "")
            if data.get("salary"):
                job["salary"] = data["salary"]
            if data.get("location"):
                job["location"] = data["location"]
            job["work_mode"] = data.get("work_mode", "not_specified")
            job["experience_years"] = data.get("experience_years", "")
            job["seniority"] = data.get("seniority", "not_specified")
            job["skills_required"] = data.get("skills_required", [])
            job["qualifications"] = data.get("qualifications", [])
            job["visa_sponsorship"] = data.get("visa_sponsorship", "not_mentioned")
            job["contract_type"] = data.get("contract_type", "not_specified")
            job["application_type"] = data.get("application_type", "not_clear")
            job["posted_date"] = data.get("posted_date", "")
            job["deadline"] = data.get("deadline", "")
            job["benefits"] = data.get("benefits", "")
            job["team_department"] = data.get("team_department", "")
            job["is_finance_role"] = data.get("is_finance_role", True)
            job["requires_office"] = data.get("requires_office", True)
            job["enrichment_status"] = "success"
            break

        elif action_type == "SCROLL":
            await page.mouse.wheel(0, random.randint(500, 900))
            await random_delay(2, 3)

        elif action_type == "CLICK":
            idx = action.get("index")
            desc = action.get("description", "")
            flush_print(f"      [ai] Click [{idx}]: {desc}")
            await click_element_by_index(page, idx)
            await random_delay(2, 4)

        elif action_type == "FAILED":
            reason = action.get("reason", "unknown")
            job["enrichment_status"] = f"failed: {reason}"
            break
    else:
        # Exhausted turns without getting JOB_DETAILS
        job["enrichment_status"] = "incomplete"

    return job


# ============================================================
# STAGE 3: FILTER — salary, relevance, requirements
# ============================================================

def parse_salary_value(salary_text: str) -> int | None:
    """Parse salary text and return the maximum annual figure in GBP.

    Returns None if salary cannot be determined numerically.
    """
    if not salary_text:
        return None

    text = salary_text.lower().replace(",", "").replace("£", "").replace("gbp", "").strip()

    # Skip non-numeric salaries
    if text in ("competitive", "market rate", "doe", "negotiable", "tbc", ""):
        return None

    # "£65k - £80k" pattern
    k_matches = re.findall(r"(\d{2,3})\s*k", text)
    if k_matches:
        return max(int(n) * 1000 for n in k_matches)

    # "65000 - 80000" or "£65,000" patterns
    full_matches = re.findall(r"(\d{5,6})", text)
    if full_matches:
        return max(int(n) for n in full_matches)

    # Daily rates — annualise at 230 working days
    day_match = re.search(r"(\d{3,4})\s*(?:per\s*day|/day|pd|daily)", text)
    if day_match:
        daily = int(day_match.group(1))
        return daily * 230

    return None


def filter_jobs(jobs: list[dict]) -> list[dict]:
    """Apply all filters using enriched data.

    Filters:
    1. Salary >= £60k (keep unknowns)
    2. Must be finance-related (keep if not enriched)
    3. Location: London area (keep if not specified)
    4. Not explicitly "no visa sponsorship"
    5. Not pure tech/engineering role
    6. Not senior/director level (mid-level candidate)
    7. Not contract/per-day roles (no visa sponsorship for contracts)
    """
    kept = []
    removed_reasons = {
        "salary_too_low": 0,
        "not_finance": 0,
        "no_sponsorship": 0,
        "wrong_location": 0,
        "too_senior": 0,
        "contract_role": 0,
    }

    for job in jobs:
        # Filter 1: Salary
        salary_text = job.get("salary", "")
        parsed_salary = parse_salary_value(salary_text)
        if parsed_salary is not None and parsed_salary < MIN_SALARY:
            removed_reasons["salary_too_low"] += 1
            continue

        # Filter 2: Contract/per-day roles (no visa for contracts)
        if salary_text and "per day" in salary_text.lower():
            removed_reasons["contract_role"] += 1
            continue
        if job.get("contract_type") in ("contract", "ftc", "temp"):
            removed_reasons["contract_role"] += 1
            continue

        # Filter 3: Too senior (candidate is mid-level)
        seniority = job.get("seniority", "")
        title = job.get("title", "").lower()
        if seniority in ("senior", "director"):
            removed_reasons["too_senior"] += 1
            continue
        if any(kw in title for kw in ["senior ", "head of", "director", "managing director", "vp ", "vice president"]):
            removed_reasons["too_senior"] += 1
            continue

        # Filter 4: Finance role (only filter if we have enrichment data)
        if job.get("enrichment_status") == "success":
            if job.get("is_finance_role") is False:
                removed_reasons["not_finance"] += 1
                continue

        # Filter 5: Visa sponsorship (only reject if explicitly "no")
        if job.get("visa_sponsorship") == "no":
            removed_reasons["no_sponsorship"] += 1
            continue

        # Filter 6: Location — must be London area
        location = job.get("location", "").lower()
        if location and "london" not in location and "uk" not in location and "united kingdom" not in location:
            # Only filter out if we're confident it's not London
            if any(city in location for city in ["manchester", "birmingham", "edinburgh", "glasgow", "leeds", "bristol", "dublin"]):
                removed_reasons["wrong_location"] += 1
                continue

        kept.append(job)

    # Report
    total_removed = sum(removed_reasons.values())
    if total_removed:
        flush_print(f"  Filtering removed {total_removed} jobs:")
        for reason, count in removed_reasons.items():
            if count:
                flush_print(f"    - {reason}: {count}")

    return kept


def score_relevance(job: dict) -> float:
    """Score a job's relevance to Nidhi's profile (0-100).

    Uses enriched data when available for better scoring.
    """
    title = job.get("title", "").lower()
    company = job.get("company", "").lower()
    description = job.get("description", "").lower()
    skills = [s.lower() for s in job.get("skills_required", [])]
    score = 0

    # === TITLE SCORING ===

    # High-value title keywords (Nidhi's core domain)
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

    # Role level (mid-level candidate)
    if any(w in title for w in ["analyst", "associate", "specialist"]):
        score += 10
    if "avp" in title or "assistant vice president" in title:
        score += 5
    if "senior" in title:
        score -= 15
    if "director" in title or "head of" in title or "managing" in title:
        score -= 30
    if "intern" in title or "graduate" in title:
        score -= 10

    # === COMPANY SCORING ===

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

    # === DESCRIPTION / SKILLS SCORING (enriched data) ===

    if description:
        # Nidhi's core skills mentioned in description
        nidhi_skills = [
            "trade settlement", "reconciliation", "prime brokerage",
            "derivatives", "fixed income", "equity swaps",
            "collateral management", "bloomberg", "murex",
            "calypso", "intellimatch", "swift", "dtcc",
        ]
        for skill in nidhi_skills:
            if skill in description:
                score += 3

    if skills:
        # Skills match
        matching_skills = [
            "trade settlement", "reconciliation", "bloomberg",
            "excel", "python", "sql", "prime brokerage",
            "derivatives", "fixed income", "swift", "murex",
        ]
        for skill in matching_skills:
            if any(skill in s for s in skills):
                score += 2

    # === SALARY SCORING ===

    salary_text = job.get("salary", "")
    parsed_salary = parse_salary_value(salary_text)
    if parsed_salary:
        if parsed_salary >= 80000:
            score += 10
        elif parsed_salary >= 65000:
            score += 5
        elif parsed_salary < 60000:
            score -= 20

    # === NEGATIVE SIGNALS ===

    # Penalize irrelevant roles
    irrelevant = [
        "software engineer", "developer", "devops", "data engineer",
        "sales", "marketing", "hr ", "human resources", "legal",
        "compliance officer", "receptionist", "admin assistant",
        "customer service", "retail",
    ]
    for kw in irrelevant:
        if kw in title:
            score -= 30

    # London bonus
    location = job.get("location", "").lower()
    if "london" in location:
        score += 5

    # Seniority match (mid-level candidate)
    seniority = job.get("seniority", "")
    if seniority == "mid":
        score += 10
    elif seniority == "junior":
        score += 3
    elif seniority == "senior":
        score -= 15
    elif seniority == "director":
        score -= 30

    # Visa sponsorship bonus
    if job.get("visa_sponsorship") == "yes":
        score += 10

    return max(0, min(100, score))


# ============================================================
# DEDUP
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


# ============================================================
# PRE-FILTER — quick elimination before expensive enrichment
# ============================================================

def pre_filter_jobs(jobs: list[dict]) -> list[dict]:
    """Quick filter to eliminate obviously irrelevant jobs BEFORE enrichment.

    This runs on title/location/company data from Stage 1 (no page visits needed).
    Aggressive on clearly wrong locations, conservative on everything else.
    """
    kept = []
    removed = 0

    # Locations that are clearly NOT London/UK
    non_uk_locations = [
        "new york", "salt lake city", "dallas", "bengaluru", "bangalore",
        "hyderabad", "mumbai", "tokyo", "singapore", "hong kong",
        "sydney", "toronto", "chicago", "houston", "san francisco",
        "los angeles", "boston", "philadelphia", "seattle", "atlanta",
        "charlotte", "wilmington", "richardson", "michigan", "pune",
        "gurgaon", "noida", "chennai", "kolkata", "frankfurt",
        "paris", "amsterdam", "madrid", "milan", "zurich", "geneva",
        "dubai", "abu dhabi", "west palm beach", "warsaw", "dublin",
        "luxembourg",
    ]

    for job in jobs:
        title = job.get("title", "").lower()
        location = job.get("location", "").lower()
        url = job.get("url", "")

        # Skip jobs without a valid URL
        if not url or not url.startswith("http"):
            removed += 1
            continue

        # Skip if location is clearly not UK
        if location:
            if any(city in location for city in non_uk_locations):
                removed += 1
                continue

        # Skip if title contains non-UK location
        if any(city in title for city in non_uk_locations):
            removed += 1
            continue

        # Skip URLs that look like category pages rather than specific jobs
        # (e.g., higher.gs.com/job/software-engineering-hyderabad)
        if "higher.gs.com/job/" in url:
            # GS URLs with only alpha slugs (no numbers) are category pages
            path_part = url.split("higher.gs.com/job/")[1].split("?")[0]
            if not any(c.isdigit() for c in path_part):
                removed += 1
                continue

        kept.append(job)

    if removed:
        flush_print(f"  Pre-filter removed {removed} obviously irrelevant jobs")

    return kept


# ============================================================
# UTILITY
# ============================================================

def flush_print(msg: str):
    """Print with immediate flush."""
    print(msg, flush=True)


def save_json(data, path: Path):
    """Save JSON data to file with parent dir creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    flush_print(f"  [saved] {path.name} ({len(data)} items)")


# ============================================================
# MAIN
# ============================================================

async def main():
    """Main AI-driven scraping pipeline: Discover → Enrich → Filter → Save.

    Supports resuming:
    - If data/jobs_discovered.json exists, skips Stage 1
    - If data/jobs_enriched.json exists, resumes Stage 2 from where it left off
    """
    flush_print("=" * 60)
    flush_print("  AI JOB SCRAPER — Nidhi's Job Pipeline")
    flush_print("  Discover → Enrich → Filter → Save")
    flush_print("  Using Claude Haiku AI agent for page understanding")
    flush_print("=" * 60)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing jobs to avoid duplicates
    existing_jobs = []
    if JOBS_JSON.exists():
        with open(JOBS_JSON) as f:
            existing_jobs = json.load(f)
    flush_print(f"\n  Existing jobs in database: {len(existing_jobs)}")

    # ==========================================
    # Check for resumable state
    # ==========================================
    resume_enrichment = False
    all_new_jobs = []

    if ENRICHED_JSON.exists():
        # Stage 2 was in progress — resume from there
        with open(ENRICHED_JSON) as f:
            all_new_jobs = json.load(f)
        flush_print(f"\n  RESUMING: Found {len(all_new_jobs)} jobs with partial enrichment")
        enriched_so_far = sum(1 for j in all_new_jobs if j.get("enrichment_status"))
        flush_print(f"  Already enriched: {enriched_so_far}/{len(all_new_jobs)}")
        resume_enrichment = True

    elif DISCOVERED_JSON.exists():
        # Stage 1 complete — skip to enrichment
        with open(DISCOVERED_JSON) as f:
            all_new_jobs = json.load(f)
        flush_print(f"\n  RESUMING: Found {len(all_new_jobs)} discovered jobs (skipping Stage 1)")

    if not all_new_jobs:
        # Fresh run — do Stage 1
        all_new_jobs = await _run_stage1(existing_jobs)

    if not resume_enrichment:
        # Pre-filter before enrichment
        flush_print(f"\n  Applying pre-filter on {len(all_new_jobs)} discovered jobs...")
        all_new_jobs = pre_filter_jobs(all_new_jobs)
        flush_print(f"  After pre-filter: {len(all_new_jobs)} jobs to enrich")

        # Save discovered jobs (so we can skip Stage 1 next time)
        save_json(all_new_jobs, DISCOVERED_JSON)

    # ==========================================
    # STAGE 2: ENRICH — deep scrape each job page
    # ==========================================
    await _run_stage2(all_new_jobs)

    # ==========================================
    # STAGE 3: FILTER — apply all criteria
    # ==========================================
    flush_print(f"\n{'='*60}")
    flush_print(f"  STAGE 3: Filtering (salary >= £{MIN_SALARY:,}, finance role, London, visa OK)")
    flush_print(f"{'='*60}")
    flush_print(f"\n  Jobs before filtering: {len(all_new_jobs)}")

    filtered_jobs = filter_jobs(all_new_jobs)
    flush_print(f"  Jobs after filtering: {len(filtered_jobs)}")

    # ==========================================
    # STAGE 4: SCORE & SAVE
    # ==========================================
    flush_print(f"\n{'='*60}")
    flush_print(f"  STAGE 4: Scoring & saving results")
    flush_print(f"{'='*60}")

    # Score relevance
    for job in filtered_jobs:
        job["relevance_score"] = score_relevance(job)

    # Sort by relevance
    filtered_jobs.sort(key=lambda j: j["relevance_score"], reverse=True)

    if filtered_jobs:
        flush_print(f"\n  {len(filtered_jobs)} jobs scored (range: {filtered_jobs[-1]['relevance_score']:.0f} - {filtered_jobs[0]['relevance_score']:.0f})")
    else:
        flush_print("\n  WARNING: No jobs survived filtering!")

    # Assign IDs
    max_existing_id = max((j.get("id", 0) for j in existing_jobs), default=0)
    for idx, job in enumerate(filtered_jobs):
        job["id"] = max_existing_id + idx + 1

    # Save results
    output_path = DATA_DIR / "jobs_scraped.json"
    save_json(filtered_jobs, output_path)

    # Combined list (all time)
    combined = existing_jobs + filtered_jobs
    combined_path = DATA_DIR / "jobs_all.json"
    save_json(combined, combined_path)

    # Markdown summary
    md_path = DATA_DIR / "jobs_scraped.md"
    with open(md_path, "w") as f:
        f.write(f"# Jobs Discovered — {time.strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(f"Pipeline: {len(all_new_jobs)} discovered → {len(filtered_jobs)} after filtering\n\n")
        f.write("| # | Score | Title | Company | Salary | Mode | Visa | Source |\n")
        f.write("|---|-------|-------|---------|--------|------|------|--------|\n")
        for job in filtered_jobs:
            f.write(
                f"| {job['id']} | {job['relevance_score']:.0f} | "
                f"{job['title'][:45]} | {job.get('company', '?')[:20]} | "
                f"{job.get('salary', '-')[:18]} | "
                f"{job.get('work_mode', '?')[:6]} | "
                f"{job.get('visa_sponsorship', '?')[:3]} | "
                f"{job.get('source', '?')} |\n"
            )
    flush_print(f"  Saved: {md_path}")

    # Clean up intermediate files after successful completion
    if ENRICHED_JSON.exists():
        ENRICHED_JSON.unlink()
    if DISCOVERED_JSON.exists():
        DISCOVERED_JSON.unlink()
    flush_print("  Cleaned up intermediate files")

    # ==========================================
    # SUMMARY
    # ==========================================
    flush_print(f"\n{'='*60}")
    flush_print(f"  PIPELINE SUMMARY")
    flush_print(f"{'='*60}")

    # Source breakdown
    sources = {}
    for j in filtered_jobs:
        src = j.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    flush_print(f"\n  By source:")
    for src, count in sorted(sources.items(), key=lambda x: -x[1]):
        flush_print(f"    {src}: {count}")

    # Data completeness
    has_salary = sum(1 for j in filtered_jobs if j.get("salary"))
    has_desc = sum(1 for j in filtered_jobs if j.get("description"))
    has_skills = sum(1 for j in filtered_jobs if j.get("skills_required"))
    flush_print(f"\n  Data completeness ({len(filtered_jobs)} jobs):")
    flush_print(f"    Salary info: {has_salary}")
    flush_print(f"    Description: {has_desc}")
    flush_print(f"    Skills listed: {has_skills}")

    # Work mode breakdown
    modes = {}
    for j in filtered_jobs:
        m = j.get("work_mode", "not_specified")
        modes[m] = modes.get(m, 0) + 1
    flush_print(f"\n  Work mode:")
    for mode, count in sorted(modes.items(), key=lambda x: -x[1]):
        flush_print(f"    {mode}: {count}")

    # Top 15 preview
    flush_print(f"\n  TOP 15 MOST RELEVANT:")
    for job in filtered_jobs[:15]:
        salary_display = f" | {job['salary']}" if job.get("salary") else ""
        visa_display = f" | visa:{job.get('visa_sponsorship', '?')}" if job.get("visa_sponsorship") not in ("not_mentioned", None) else ""
        flush_print(f"    [{job['relevance_score']:3.0f}] {job['title'][:55]}")
        flush_print(f"         {job.get('company', '?')[:30]} | {job.get('work_mode', '?')}{salary_display}{visa_display}")

    flush_print(f"\n  Done! {len(filtered_jobs)} enriched jobs ready for application.")


async def _run_stage1(existing_jobs: list[dict]) -> list[dict]:
    """Stage 1: Discover jobs from all sources. Returns deduped list."""
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

        # --- 1a: Company Career Pages ---
        flush_print(f"\n{'='*60}")
        flush_print(f"  STAGE 1a: Discovering jobs from {len(COMPANY_CAREER_PAGES)} career pages")
        flush_print(f"{'='*60}\n")

        for idx, company_info in enumerate(COMPANY_CAREER_PAGES):
            company = company_info["company"]
            flush_print(f"  [{idx+1}/{len(COMPANY_CAREER_PAGES)}] {company}")

            jobs = await ai_scrape_search_results(
                page, company_info["url"], "career_page", f"{company} careers", max_pages=2
            )

            if len(jobs) < 3 and "alt_url" in company_info:
                flush_print(f"    Few results — trying alt URL...")
                alt_jobs = await ai_scrape_search_results(
                    page, company_info["alt_url"], "career_page", f"{company} careers (alt)", max_pages=2
                )
                jobs.extend(alt_jobs)

            for j in jobs:
                if not j.get("company"):
                    j["company"] = company

            all_new_jobs.extend(jobs)
            flush_print(f"    -> {len(jobs)} jobs from {company}")
            await random_delay(5, 12)

        flush_print(f"\n  Stage 1a: {len(all_new_jobs)} jobs from career pages")

        # --- 1b: Reed.co.uk ---
        flush_print(f"\n{'='*60}")
        flush_print(f"  STAGE 1b: Discovering jobs from Reed.co.uk ({len(REED_SEARCH_QUERIES)} queries)")
        flush_print(f"{'='*60}\n")

        reed_count_before = len(all_new_jobs)
        for idx, query in enumerate(REED_SEARCH_QUERIES):
            flush_print(f"  [{idx+1}/{len(REED_SEARCH_QUERIES)}] Reed: '{query}'")
            url = (
                f"https://www.reed.co.uk/jobs/{quote_plus(query)}-jobs-in-london"
                f"?sortby=DisplayDate&proximity=10"
            )
            jobs = await ai_scrape_search_results(page, url, "reed", query, max_pages=3)
            all_new_jobs.extend(jobs)
            flush_print(f"    -> {len(jobs)} jobs")
            await random_delay(8, 15)

        flush_print(f"\n  Stage 1b: {len(all_new_jobs) - reed_count_before} jobs from Reed")

        # --- 1c: LinkedIn ---
        if linkedin_logged_in:
            flush_print(f"\n{'='*60}")
            flush_print(f"  STAGE 1c: Discovering jobs from LinkedIn ({len(LINKEDIN_SEARCH_QUERIES)} queries)")
            flush_print(f"  Delays: 15-30s between, 60-120s break every 8")
            flush_print(f"{'='*60}\n")

            linkedin_count_before = len(all_new_jobs)
            for idx, query in enumerate(LINKEDIN_SEARCH_QUERIES):
                flush_print(f"  [{idx+1}/{len(LINKEDIN_SEARCH_QUERIES)}] LinkedIn: '{query}'")

                params = {
                    "keywords": query,
                    "location": LOCATION,
                    "geoId": LOCATION_GEOID,
                    "f_TPR": "r2592000",
                    "f_E": "2,3,4",
                    "f_AL": "true",  # Easy Apply only
                    "sortBy": "DD",
                }
                url = f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"

                jobs = await ai_scrape_search_results(page, url, "linkedin", query, max_pages=3)
                all_new_jobs.extend(jobs)
                flush_print(f"    -> {len(jobs)} jobs (total: {len(all_new_jobs)})")

                delay = random.uniform(15, 30)
                flush_print(f"    Waiting {delay:.0f}s...")
                await asyncio.sleep(delay)

                if (idx + 1) % 8 == 0 and idx < len(LINKEDIN_SEARCH_QUERIES) - 1:
                    long_delay = random.uniform(60, 120)
                    flush_print(f"\n  --- Break {long_delay:.0f}s (anti-detection) ---\n")
                    await asyncio.sleep(long_delay)

            flush_print(f"\n  Stage 1c: {len(all_new_jobs) - linkedin_count_before} jobs from LinkedIn")
        else:
            flush_print("\n  SKIPPING LinkedIn (not logged in)")

        flush_print(f"\n  STAGE 1 COMPLETE: {len(all_new_jobs)} raw jobs discovered")

        # Deduplicate
        all_new_jobs = deduplicate_jobs(all_new_jobs, existing_jobs)
        flush_print(f"  After dedup: {len(all_new_jobs)} unique jobs")

        # Save session
        await context.storage_state(path=str(STORAGE_STATE))
        await browser.close()

    return all_new_jobs


async def _run_stage2(all_new_jobs: list[dict]):
    """Stage 2: Enrich each job with full page data. Saves progress every 10 jobs."""
    # Find where to resume from
    start_idx = 0
    for i, job in enumerate(all_new_jobs):
        if not job.get("enrichment_status"):
            start_idx = i
            break
    else:
        # All already enriched
        flush_print(f"\n  Stage 2: All {len(all_new_jobs)} jobs already enriched — skipping")
        return

    remaining = len(all_new_jobs) - start_idx
    flush_print(f"\n{'='*60}")
    flush_print(f"  STAGE 2: Enriching jobs (starting at {start_idx+1}/{len(all_new_jobs)}, {remaining} remaining)")
    flush_print(f"  Extracting: description, salary, skills, visa, work mode, etc.")
    flush_print(f"  Progress saved every 10 jobs — safe to stop anytime")
    flush_print(f"{'='*60}\n")

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

        if STORAGE_STATE.exists():
            context_options["storage_state"] = str(STORAGE_STATE)

        context = await browser.new_context(**context_options)
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = await context.new_page()

        enriched_count = sum(1 for j in all_new_jobs if j.get("enrichment_status") == "success")
        failed_count = sum(1 for j in all_new_jobs if j.get("enrichment_status") and j.get("enrichment_status") != "success")

        for idx in range(start_idx, len(all_new_jobs)):
            job = all_new_jobs[idx]
            flush_print(f"  [{idx+1}/{len(all_new_jobs)}] {job['title'][:50]} @ {job.get('company', '?')[:25]}")

            await ai_enrich_job(page, job)

            status = job.get("enrichment_status", "unknown")
            if status == "success":
                enriched_count += 1
                salary_info = f" | Salary: {job.get('salary')}" if job.get("salary") else ""
                flush_print(f"      OK: {job.get('work_mode', '?')} | {job.get('seniority', '?')}{salary_info}")
            else:
                failed_count += 1
                flush_print(f"      {status}")

            # Anti-detection delays
            await random_delay(5, 10)

            # Save progress every 10 jobs
            if (idx + 1) % 10 == 0:
                save_json(all_new_jobs, ENRICHED_JSON)

                if (idx + 1) < len(all_new_jobs):
                    long_delay = random.uniform(30, 60)
                    flush_print(f"\n  --- Break ({long_delay:.0f}s) after {idx+1} enrichments | {enriched_count} OK, {failed_count} failed ---\n")
                    await asyncio.sleep(long_delay)

        # Final save
        save_json(all_new_jobs, ENRICHED_JSON)
        flush_print(f"\n  STAGE 2 COMPLETE: {enriched_count} enriched, {failed_count} failed")

        # Save session
        await context.storage_state(path=str(STORAGE_STATE))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
