"""Fresh LinkedIn Easy Apply — Searches for jobs and applies in real-time.

Does a fresh search on LinkedIn (logged in) with Easy Apply filter,
then applies to each job immediately. This avoids stale/expired URLs.

Usage:
    python3 -u fresh_easy_apply.py
"""

import asyncio
import csv
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

from config import DATA_DIR, OUTPUT_DIR, RESUME_PATH, MODE
from ai_navigator import get_client, dismiss_overlays
from profile_tools import (
    FORM_TOOLS, execute_lookup, build_tool_system_prompt,
    set_current_job, get_cover_letter_for_job,
)
from linkedin_apply import get_dialog_elements, _execute_tool_call

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

STORAGE_FILE = DATA_DIR / "storage_state.json"
PROGRESS_FILE = DATA_DIR / "fresh_easy_apply_progress.json"
LOG_FILE = OUTPUT_DIR / "fresh_easy_apply_log.csv"

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")

# Search queries relevant to Nidhi's profile
SEARCH_QUERIES = [
    "trade operations",
    "middle office",
    "settlement analyst",
    "reconciliation analyst",
    "fund operations",
    "financial operations analyst",
    "trade support",
    "post trade",
    "investment operations",
    "clearing and settlement",
    "securities operations",
    "treasury analyst",
    "finance analyst operations",
    "banking operations",
    "asset servicing",
    "custody analyst",
    "collateral management",
    "derivatives operations",
    "prime brokerage",
    "corporate actions",
]


# ---------------------------------------------------------------------------
# Search and collect jobs
# ---------------------------------------------------------------------------

async def search_easy_apply_jobs(page: Page) -> list[dict]:
    """Search LinkedIn for Easy Apply jobs across multiple queries."""
    all_jobs = []
    seen_ids = set()

    for query in SEARCH_QUERIES:
        # Build search URL with filters:
        # f_AL=true (Easy Apply), f_JT=F (Full-time), f_E=2,3,4 (Entry/Assoc/Mid)
        # f_WT=1,2 (On-site, Remote), sortBy=DD (Most recent)
        search_url = (
            f"https://www.linkedin.com/jobs/search/?"
            f"keywords={query.replace(' ', '%20')}"
            f"&location=London%2C%20England%2C%20United%20Kingdom"
            f"&f_AL=true"
            f"&f_JT=F"
            f"&f_E=2%2C3%2C4"
            f"&sortBy=DD"
        )

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(random.uniform(4, 6))

            # Get job listings from the page
            jobs = await page.evaluate(r"""() => {
                const results = [];
                const cards = document.querySelectorAll('[data-occludable-job-id], .job-card-container, .jobs-search-results__list-item');
                for (const card of cards) {
                    const titleEl = card.querySelector('a[class*="title"], .job-card-list__title a, a[href*="/jobs/view/"]');
                    const companyEl = card.querySelector('.job-card-container__primary-description, [class*="company"], .artdeco-entity-lockup__subtitle');
                    const locationEl = card.querySelector('.job-card-container__metadata-item, [class*="location"]');

                    const title = (titleEl?.innerText || '').trim();
                    const url = titleEl?.href || '';
                    const company = (companyEl?.innerText || '').trim();
                    const location = (locationEl?.innerText || '').trim();

                    if (title && url && url.includes('/jobs/view/')) {
                        // Extract job ID from URL
                        const match = url.match(/\/jobs\/view\/(\d+)/);
                        const jobId = match ? match[1] : '';
                        results.push({title, url: url.split('?')[0], company, location, jobId});
                    }
                }
                return results;
            }""")

            new_count = 0
            for job in jobs:
                job_id = job.get("jobId", "")
                if job_id and job_id not in seen_ids:
                    seen_ids.add(job_id)
                    job["search_query"] = query
                    all_jobs.append(job)
                    new_count += 1

            print(f"    '{query}': {len(jobs)} found, {new_count} new")

            # Scroll down to load more results
            for scroll in range(2):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
                more_jobs = await page.evaluate(r"""() => {
                    const results = [];
                    const cards = document.querySelectorAll('[data-occludable-job-id], .job-card-container');
                    for (const card of cards) {
                        const titleEl = card.querySelector('a[href*="/jobs/view/"]');
                        const companyEl = card.querySelector('.job-card-container__primary-description, [class*="company"]');
                        const title = (titleEl?.innerText || '').trim();
                        const url = titleEl?.href || '';
                        const company = (companyEl?.innerText || '').trim();
                        if (title && url && url.includes('/jobs/view/')) {
                            const match = url.match(/\/jobs\/view\/(\d+)/);
                            const jobId = match ? match[1] : '';
                            results.push({title, url: url.split('?')[0], company, jobId});
                        }
                    }
                    return results;
                }""")
                for job in more_jobs:
                    job_id = job.get("jobId", "")
                    if job_id and job_id not in seen_ids:
                        seen_ids.add(job_id)
                        job["search_query"] = query
                        all_jobs.append(job)

        except Exception as e:
            print(f"    '{query}': ERROR - {str(e)[:50]}")

        # Rate limiting between searches
        await asyncio.sleep(random.uniform(5, 10))

    return all_jobs


# ---------------------------------------------------------------------------
# Apply to a single job
# ---------------------------------------------------------------------------

async def apply_to_job(page: Page, job: dict, client) -> tuple[str, str]:
    """Navigate to job, click Easy Apply, fill form, submit."""
    url = job.get("url", "")

    # Navigate to the job
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    except Exception as e:
        return "navigation_error", str(e)[:80]

    await asyncio.sleep(random.uniform(3, 5))

    # Check if job is valid
    try:
        body = await page.inner_text("body")
        body_lower = body.lower()
        if "unable to load" in body_lower or "removed" in body_lower:
            return "expired", "Job removed"
        if "no longer accepting" in body_lower or "closed" in body_lower:
            return "expired", "Job closed"
    except Exception:
        pass

    # Find Easy Apply button (LinkedIn uses <a> or <button>, with hashed classes)
    easy_apply_btn = None
    selectors = [
        '[aria-label*="Easy Apply to"]',
        'a:has-text("Easy Apply")',
        'button:has-text("Easy Apply")',
        '.jobs-apply-button',
        'button[aria-label*="Easy Apply"]',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                cls = await btn.get_attribute("class") or ""
                if "filter" not in cls and "pill" not in cls and "search-reusables" not in cls:
                    easy_apply_btn = btn
                    break
        except Exception:
            continue

    if not easy_apply_btn:
        return "no_button", "Easy Apply button not found"

    # Click Easy Apply
    await easy_apply_btn.click()
    await asyncio.sleep(random.uniform(2, 4))

    # Wait for modal
    try:
        await page.wait_for_selector(
            '[role="dialog"]:visible, .artdeco-modal:visible',
            timeout=8000
        )
    except Exception:
        # Try clicking again
        try:
            await easy_apply_btn.click()
            await asyncio.sleep(3)
            await page.wait_for_selector('[role="dialog"]:visible', timeout=5000)
        except Exception:
            return "no_modal", "Easy Apply modal didn't open"

    # AI fills the form
    status, reason = await fast_fill_form(page, job, client)

    # Auto-submit if scanned
    if status == "scanned":
        submit_btn = page.locator(
            'button:has-text("Submit application"), '
            'button:has-text("Submit"), '
            'button[aria-label*="Submit"]'
        ).first
        try:
            if await submit_btn.is_visible(timeout=3000):
                await submit_btn.click()
                await asyncio.sleep(3)
                # Check for success
                try:
                    body = await page.inner_text("body")
                    if "submitted" in body.lower() or "thank" in body.lower():
                        return "applied", "Application submitted"
                except Exception:
                    pass
                return "applied", "Submit clicked"
        except Exception:
            pass
        return "scanned", "Filled but couldn't submit"

    # Close modal on failure
    if status not in ("applied",):
        try:
            close_btn = page.locator('button[aria-label="Dismiss"], button[aria-label="Close"]').first
            if await close_btn.is_visible(timeout=2000):
                await close_btn.click()
                await asyncio.sleep(1)
            # Handle "discard" confirmation
            discard_btn = page.locator('button:has-text("Discard"), button[data-test-modal-close-btn]').first
            if await discard_btn.is_visible(timeout=2000):
                await discard_btn.click()
                await asyncio.sleep(1)
        except Exception:
            pass

    return status, reason


async def fast_fill_form(page: Page, job: dict, client) -> tuple[str, str]:
    """Fill Easy Apply form using AI tools."""
    resume_path = str(RESUME_PATH)
    cl_path = get_cover_letter_for_job(job) or ""
    set_current_job(job)
    system_prompt = build_tool_system_prompt(job, resume_path, cl_path)
    messages = []
    model = os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1")

    for step in range(25):
        try:
            interactive = await get_dialog_elements(page)
        except Exception:
            await asyncio.sleep(2)
            continue

        if not interactive or not interactive.strip():
            await asyncio.sleep(2)
            continue

        messages.append({"role": "user", "content": f"Step {step + 1}:\n\n{interactive}"})

        try:
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                messages=messages[-6:],
                tools=FORM_TOOLS,
            )
        except Exception as e:
            return "api_error", str(e)[:100]

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            tool_results = []
            done_status = None
            done_reason = ""

            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name == "done":
                    done_status = block.input.get("status", "scanned")
                    done_reason = block.input.get("reason", "")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"done: {done_status}",
                    })
                else:
                    result = await _execute_tool_call(page, block.name, block.input, resume_path, cl_path)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "user", "content": tool_results})
            if done_status:
                return done_status, done_reason

        elif response.stop_reason == "end_turn":
            pass

    return "max_steps", "Reached 25 steps"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_result(job: dict, status: str, reason: str):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "title", "company", "url", "status", "reason"])
        writer.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S"),
            job.get("title", "")[:100],
            job.get("company", "")[:50],
            job.get("url", "")[:200],
            status,
            reason[:200],
        ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 70)
    print("  FRESH LINKEDIN EASY APPLY")
    print("  Search → Apply in real-time (no stale URLs)")
    print("=" * 70, flush=True)

    # Load progress
    processed_ids = set()
    if PROGRESS_FILE.exists():
        processed_ids = set(json.loads(PROGRESS_FILE.read_text()))
        print(f"  Previously applied job IDs: {len(processed_ids)}")

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
        print("  Verifying LinkedIn login...")
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)

        if "login" in page.url.lower():
            print("  Logging in...")
            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            await asyncio.sleep(3)
            for sel in ['#username', 'input[name="session_key"]']:
                try:
                    if await page.locator(sel).is_visible(timeout=3000):
                        await page.fill(sel, LINKEDIN_EMAIL)
                        break
                except Exception:
                    continue
            for sel in ['#password', 'input[name="session_password"]']:
                try:
                    if await page.locator(sel).is_visible(timeout=2000):
                        await page.fill(sel, LINKEDIN_PASSWORD)
                        break
                except Exception:
                    continue
            await page.locator('button[type="submit"]').click()
            await asyncio.sleep(5)

            if "checkpoint" in page.url.lower():
                print("  Verification needed — waiting 60s...")
                await asyncio.sleep(60)

            if "login" in page.url.lower():
                print("  ERROR: Login failed")
                await browser.close()
                return

            state = await context.storage_state()
            STORAGE_FILE.write_text(json.dumps(state))
            print("  Logged in!")
        else:
            print("  Session valid!")

        # Dismiss overlays
        await dismiss_overlays(page)

        # Step 1: Search for jobs
        print(f"\n  Step 1: Searching for Easy Apply jobs...\n")
        jobs = await search_easy_apply_jobs(page)
        print(f"\n  Total unique jobs found: {len(jobs)}")

        # Filter out already processed
        new_jobs = [j for j in jobs if j.get("jobId", "") not in processed_ids]
        print(f"  New (not yet applied): {len(new_jobs)}")

        # Save found jobs
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "fresh_easy_apply_jobs.json").write_text(json.dumps(jobs, indent=2))

        if not new_jobs:
            print("  No new jobs to apply to!")
            await browser.close()
            return

        # Step 2: Apply to each job
        print(f"\n  Step 2: Applying to {len(new_jobs)} jobs...\n")

        client = get_client()
        applied = 0
        failed = 0
        expired = 0

        for idx, job in enumerate(new_jobs):
            title = job.get("title", "Unknown")[:50]
            company = job.get("company", "")[:25]
            print(f"  [{idx+1}/{len(new_jobs)}] {title} | {company}")

            try:
                status, reason = await asyncio.wait_for(
                    apply_to_job(page, job, client), timeout=120
                )

                if status == "applied":
                    applied += 1
                    print(f"    APPLIED!")
                elif status == "expired":
                    expired += 1
                    print(f"    Expired: {reason[:50]}")
                elif status == "session_expired":
                    print(f"    SESSION EXPIRED — stopping")
                    break
                else:
                    failed += 1
                    print(f"    {status}: {reason[:50]}")

                log_result(job, status, reason)

            except asyncio.TimeoutError:
                failed += 1
                print(f"    TIMEOUT (120s)")
                log_result(job, "timeout", "Exceeded 120s")
                # Close any modal
                try:
                    close_btn = page.locator('button[aria-label="Dismiss"]').first
                    if await close_btn.is_visible(timeout=1000):
                        await close_btn.click()
                except Exception:
                    pass
            except Exception as e:
                failed += 1
                print(f"    ERROR: {str(e)[:60]}")
                log_result(job, "error", str(e)[:200])

            # Mark as processed
            job_id = job.get("jobId", "")
            if job_id:
                processed_ids.add(job_id)
                PROGRESS_FILE.write_text(json.dumps(list(processed_ids)))

            # Rate limit: 8-15s between applications
            await asyncio.sleep(random.uniform(8, 15))

            if (idx + 1) % 10 == 0:
                print(f"\n  --- Progress: {idx+1}/{len(new_jobs)} | Applied: {applied} | Expired: {expired} | Failed: {failed} ---\n", flush=True)

        print(f"\n{'=' * 70}")
        print(f"  COMPLETE")
        print(f"  Applied: {applied}")
        print(f"  Expired: {expired}")
        print(f"  Failed: {failed}")
        print(f"  Log: {LOG_FILE}")
        print(f"{'=' * 70}")

        # Save session
        try:
            state = await context.storage_state()
            STORAGE_FILE.write_text(json.dumps(state))
        except Exception:
            pass

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
