"""eFinancialCareers Description Fetcher — Visits each job URL to get full details.

Reads efc_jobs_raw.json (from fetch_efinancial_jobs.py),
visits each job page, extracts full description/requirements/salary,
and saves to efc_jobs_with_descriptions.json.

Usage:
    python3 -u fetch_efc_descriptions.py
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
EFC_JOBS_RAW_FILE = DATA_DIR / "efc_jobs_raw.json"
EFC_JOBS_WITH_DESC_FILE = DATA_DIR / "efc_jobs_with_descriptions.json"
EFC_DESC_PROGRESS_FILE = DATA_DIR / "efc_desc_progress.json"
EFC_STORAGE_FILE = DATA_DIR / "efinancial_storage_state.json"


# ---------------------------------------------------------------------------
# Extract job details from page
# ---------------------------------------------------------------------------

async def extract_job_details(page: Page) -> dict:
    """Extract full job details from an individual job page."""
    details = await page.evaluate("""() => {
        const result = {
            description: '',
            salary: '',
            job_type: '',
            seniority: '',
            requirements: '',
            posted_date: '',
            company: '',
            location: '',
            full_text: '',
        };

        // Get full page text for keyword matching later
        result.full_text = document.body.innerText.substring(0, 5000);

        // Description — look for main content area
        const descSelectors = [
            '[class*="description"], [class*="Description"]',
            '[data-test*="description"]',
            '[class*="job-detail"], [class*="JobDetail"]',
            '[class*="content"], [class*="Content"]',
            'article',
            '.job-description',
            '#job-description',
        ];

        for (const sel of descSelectors) {
            const el = document.querySelector(sel);
            if (el && el.innerText.length > 100) {
                result.description = el.innerText.trim().substring(0, 4000);
                break;
            }
        }

        // If no description found, try the largest text block
        if (!result.description) {
            const allDivs = document.querySelectorAll('div, section');
            let longest = '';
            for (const div of allDivs) {
                const text = div.innerText || '';
                if (text.length > longest.length && text.length < 10000) {
                    longest = text;
                }
            }
            if (longest.length > 200) {
                result.description = longest.substring(0, 4000);
            }
        }

        // Salary
        const salarySelectors = [
            '[class*="salary"], [class*="Salary"]',
            '[data-test*="salary"]',
            '[class*="compensation"], [class*="Compensation"]',
        ];
        for (const sel of salarySelectors) {
            const el = document.querySelector(sel);
            if (el) {
                result.salary = el.innerText.trim().substring(0, 200);
                break;
            }
        }

        // Try to find salary in text
        if (!result.salary) {
            const body = document.body.innerText;
            const salaryMatch = body.match(/[£$€]\\s*[\\d,]+(?:\\s*[-–to]+\\s*[£$€]?\\s*[\\d,]+)?\\s*(?:per annum|pa|p\\.a\\.|per year)?/i);
            if (salaryMatch) {
                result.salary = salaryMatch[0].trim();
            }
        }

        // Job type (permanent, contract, etc)
        const typeSelectors = [
            '[class*="job-type"], [class*="JobType"]',
            '[class*="employment-type"], [class*="EmploymentType"]',
            '[data-test*="type"]',
        ];
        for (const sel of typeSelectors) {
            const el = document.querySelector(sel);
            if (el) {
                result.job_type = el.innerText.trim().substring(0, 100);
                break;
            }
        }

        // Seniority level
        const senioritySelectors = [
            '[class*="seniority"], [class*="Seniority"]',
            '[class*="level"], [class*="Level"]',
            '[data-test*="seniority"]',
        ];
        for (const sel of senioritySelectors) {
            const el = document.querySelector(sel);
            if (el) {
                result.seniority = el.innerText.trim().substring(0, 100);
                break;
            }
        }

        // Company (might be better on individual page)
        const companySelectors = [
            '[class*="company-name"], [class*="CompanyName"]',
            '[class*="employer"], [class*="Employer"]',
            '[data-test*="company"]',
            'a[href*="/company/"]',
        ];
        for (const sel of companySelectors) {
            const el = document.querySelector(sel);
            if (el) {
                result.company = el.innerText.trim().substring(0, 150);
                break;
            }
        }

        // Location
        const locationSelectors = [
            '[class*="location"], [class*="Location"]',
            '[data-test*="location"]',
        ];
        for (const sel of locationSelectors) {
            const el = document.querySelector(sel);
            if (el) {
                result.location = el.innerText.trim().substring(0, 150);
                break;
            }
        }

        // Posted date
        const dateSelectors = [
            '[class*="posted"], [class*="Posted"]',
            '[class*="date"], [class*="Date"]',
            'time',
        ];
        for (const sel of dateSelectors) {
            const el = document.querySelector(sel);
            if (el) {
                result.posted_date = el.innerText.trim().substring(0, 50);
                break;
            }
        }

        return result;
    }""")
    return details


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("  eFinancialCareers — Description Fetcher")
    print("=" * 60, flush=True)

    # Load raw jobs
    if not EFC_JOBS_RAW_FILE.exists():
        print("  ERROR: efc_jobs_raw.json not found. Run fetch_efinancial_jobs.py first.")
        return

    raw_jobs = json.loads(EFC_JOBS_RAW_FILE.read_text())
    print(f"  Raw jobs loaded: {len(raw_jobs)}")

    # Load progress
    processed_urls = set()
    enriched_jobs = []
    if EFC_DESC_PROGRESS_FILE.exists():
        progress = json.loads(EFC_DESC_PROGRESS_FILE.read_text())
        enriched_jobs = progress.get("jobs", [])
        processed_urls = {j["url"] for j in enriched_jobs}
        print(f"  Resuming: {len(enriched_jobs)} already fetched")

    remaining = [j for j in raw_jobs if j["url"] not in processed_urls]
    print(f"  Remaining: {len(remaining)}")

    if not remaining:
        print("  All jobs already processed!")
        EFC_JOBS_WITH_DESC_FILE.write_text(json.dumps(enriched_jobs, indent=2))
        return

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

        success_count = 0
        fail_count = 0

        for idx, job in enumerate(remaining):
            url = job["url"]
            title = job.get("title", "Unknown")[:60]

            print(f"\n  [{idx+1}/{len(remaining)}] {title}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(1.5, 3))

                # Check for login redirect
                if "login" in page.url.lower() or "signin" in page.url.lower():
                    print(f"    Session expired — stopping")
                    break

                # Check for 404/error
                page_text = await page.inner_text("body")
                if "page not found" in page_text.lower() or "404" in page_text[:100]:
                    print(f"    404 — skipping")
                    enriched_jobs.append({**job, "description": "", "status": "404"})
                    processed_urls.add(url)
                    fail_count += 1
                    continue

                # Extract details
                details = await extract_job_details(page)

                # Merge with original job data
                enriched = {
                    "url": url,
                    "title": job.get("title", ""),
                    "company": details.get("company") or job.get("company", ""),
                    "location": details.get("location") or job.get("location", ""),
                    "salary": details.get("salary") or job.get("salary", ""),
                    "job_type": details.get("job_type", ""),
                    "seniority": details.get("seniority", ""),
                    "posted_date": details.get("posted_date") or job.get("posted", ""),
                    "description": details.get("description", ""),
                    "search_query": job.get("search_query", ""),
                    "status": "ok" if details.get("description") else "no_desc",
                }

                enriched_jobs.append(enriched)
                processed_urls.add(url)

                desc_len = len(details.get("description", ""))
                if desc_len > 100:
                    success_count += 1
                    print(f"    OK — desc: {desc_len} chars, salary: {enriched['salary'][:40]}")
                else:
                    fail_count += 1
                    print(f"    Weak desc ({desc_len} chars)")

            except Exception as e:
                enriched_jobs.append({**job, "description": "", "status": f"error: {str(e)[:100]}"})
                processed_urls.add(url)
                fail_count += 1
                print(f"    ERROR: {str(e)[:80]}")

            # Save progress every 10 jobs
            if (idx + 1) % 10 == 0:
                EFC_DESC_PROGRESS_FILE.write_text(json.dumps({"jobs": enriched_jobs}))
                print(f"\n    --- Progress: {idx+1}/{len(remaining)} | OK: {success_count} | Failed: {fail_count} ---", flush=True)

            # Rate limiting
            await asyncio.sleep(random.uniform(1, 2.5))

        # Final save
        EFC_DESC_PROGRESS_FILE.write_text(json.dumps({"jobs": enriched_jobs}))
        EFC_JOBS_WITH_DESC_FILE.write_text(json.dumps(enriched_jobs, indent=2))

        print(f"\n{'=' * 60}")
        print(f"  COMPLETE")
        print(f"  Total processed: {len(enriched_jobs)}")
        print(f"  With descriptions: {success_count}")
        print(f"  Failed/weak: {fail_count}")
        print(f"  Saved to: {EFC_JOBS_WITH_DESC_FILE}")
        print(f"{'=' * 60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
