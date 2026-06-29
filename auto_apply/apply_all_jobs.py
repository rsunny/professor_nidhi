"""Multi-Agent Job Application Orchestrator.

Pure Python logic — NO AI calls in this file. Coordinates specialized agents:
- PageClassifierAgent: "What am I looking at?"
- AuthAgent: Sign in / create account / OAuth
- GmailAgent: Get verification codes
- NavigationAgent: Find and click Apply/Next/Submit
- WorkdayFormAgent: Fill Workday forms page-by-page
- GreenhouseFormAgent: Fill Greenhouse forms programmatically
- LeverFormAgent: Fill Lever forms programmatically
- GenericFormAgent: Fill any other form with AI

Flow per job:
1. Navigate to job URL
2. Classify page
3. Handle LinkedIn job page (Easy Apply or external redirect)
4. Handle authentication if needed
5. Route to appropriate form agent based on platform
6. Log result

Usage:
    cd auto_apply && python3 -u apply_all_jobs.py
"""

import asyncio
import csv
import json
import os
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, BrowserContext

load_dotenv(Path(__file__).parent / ".env")

from config import DATA_DIR, OUTPUT_DIR, RESUME_PATH
from ai_navigator import get_client, dismiss_overlays
from linkedin_apply import handle_easy_apply
from profile_tools import get_cover_letter_for_job, set_current_job
from humanizer import random_delay

# Import agents
from agents import AgentResult, check_success_indicators
from agents.page_classifier import classify_page, PageClassification
from agents.auth_agent import authenticate, handle_linkedin_signin
from agents.navigation_agent import click_apply_button, click_next_button
from agents.workday_agent import workday_orchestrator_loop
from agents.greenhouse_agent import fill_greenhouse_form
from agents.lever_agent import fill_lever_form
from agents.generic_form_agent import generic_form_loop


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RESULTS_FILE = DATA_DIR / "careers_scrape_results.json"
STORAGE_FILE = DATA_DIR / "storage_state.json"
PROGRESS_FILE = DATA_DIR / "apply_all_progress.json"
LOG_FILE = OUTPUT_DIR / "apply_all_log.csv"
FAILED_JOBS_FILE = OUTPUT_DIR / "apply_all_failed.json"
RECORDING_JOBS_FILE = OUTPUT_DIR / "jobs_needing_recording.json"
COVER_LETTERS_DIR = OUTPUT_DIR / "cover_letters_generated"

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")

# Timeout budget per job (seconds)
JOB_TIMEOUT = 280  # Total max per job (external forms)
JOB_TIMEOUT_EASY_APPLY = 90  # LinkedIn Easy Apply only (detected at runtime)

PAUSE_EVERY_N = 25  # Pause every N jobs
PAUSE_DURATION = 45  # Seconds to pause


# ---------------------------------------------------------------------------
# Cover letter generation
# ---------------------------------------------------------------------------

def sanitize_text(text: str) -> str:
    replacements = {
        '\u2013': '-', '\u2014': '-', '\u2018': "'", '\u2019': "'",
        '\u201c': '"', '\u201d': '"', '\u2026': '...', '\u2022': '-', '\u00a0': ' ',
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text.encode('latin-1', errors='replace').decode('latin-1')


def generate_cover_letter(client, job: dict, desc: str = "") -> str:
    """Generate a cover letter PDF for this job."""
    from fpdf import FPDF
    COVER_LETTERS_DIR.mkdir(parents=True, exist_ok=True)

    title = job.get("title", "Unknown")
    company = job.get("company", "the company")

    prompt = f"""Write a concise cover letter (max 250 words) for Nidhi Shetty applying to:
JOB: {title} at {company}
DESCRIPTION: {desc[:1000]}

NIDHI'S BACKGROUND:
- 2.5 years at Morgan Stanley (Prime Brokerage, Glasgow): trade settlement, reconciliation, counterparty payments, FX reporting, month-end close
- Previous: 2.5 years at Mphasis (operations analyst) - process optimization, data validation, stakeholder management
- MSc Investment & Risk Finance (Distinction), University of Westminster 2022
- BSc Accounting & Finance, Mumbai University
- Skills: Excel/VBA, Bloomberg, reconciliation, trade ops, data analysis

INSTRUCTIONS:
- Professional tone, first person
- Connect her experience to this specific role
- Do NOT mention visa/sponsorship
- Do NOT use em-dash or en-dash characters
- Sign off as Nidhi Shetty"""

    try:
        response = client.messages.create(
            model=os.getenv("FORM_FILL_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        letter_text = sanitize_text(response.content[0].text.strip())
    except Exception:
        letter_text = (
            f"Dear Hiring Manager,\n\nI am writing to apply for the {title} position at {company}. "
            f"With 5 years of experience in financial operations including 2.5 years at Morgan Stanley "
            f"Prime Brokerage, I am confident I can contribute to your team.\n\nYours sincerely,\nNidhi Shetty"
        )

    safe_name = re.sub(r'[^\w\s-]', '', f"{company}_{title}")[:60].strip()
    filepath = COVER_LETTERS_DIR / f"cl_{safe_name.replace(' ', '_')}.pdf"

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=25)
    pdf.set_font('Helvetica', size=11)
    pdf.set_left_margin(25)
    pdf.set_right_margin(25)
    for line in letter_text.split('\n'):
        if line.strip() == '':
            pdf.ln(6)
        else:
            pdf.multi_cell(0, 6, line.strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(filepath))

    return str(filepath)


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------

def normalize_linkedin_url(url: str) -> str:
    """Convert uk.linkedin.com/jobs/view/slug-123456 to www.linkedin.com/jobs/view/123456/"""
    job_id_match = re.search(r'(\d{5,})(?:\?|$|/)', url)
    if job_id_match and "uk.linkedin.com" in url:
        return f"https://www.linkedin.com/jobs/view/{job_id_match.group(1)}/"
    return url


# ---------------------------------------------------------------------------
# Process a single job — the main orchestration logic
# ---------------------------------------------------------------------------

async def process_job(page: Page, context: BrowserContext, job: dict, client) -> tuple[str, str]:
    """Process one job application. Returns (status, reason).

    This is the orchestrator — pure Python logic, no AI calls.
    Routes to specialized agents based on page classification.
    """
    url = job.get("url", "")
    if not url:
        return "no_url", "No URL available"

    # Normalize LinkedIn URLs
    if "linkedin.com" in url:
        url = normalize_linkedin_url(url)

    # Step 1: Navigate to job page
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        return "navigation_error", f"Failed to load: {str(e)[:80]}"

    await random_delay(3, 5)
    await dismiss_overlays(page)

    # Step 2: Classify the page
    page_info = await classify_page(page)

    # Handle expired jobs immediately
    if page_info.page_type == "expired":
        return "expired", "Job no longer available"

    # Handle success (already applied)
    if page_info.page_type == "success":
        return "already_applied", "Already applied (success page shown)"

    # Handle CAPTCHA
    if page_info.page_type == "captcha":
        return "captcha", "CAPTCHA detected — cannot proceed"

    # Step 3: Handle LinkedIn job pages
    if "linkedin.com" in page.url:
        return await _handle_linkedin_page(page, context, job, client, page_info)

    # Step 4: Handle login if needed
    if page_info.page_type == "login":
        auth_result = await authenticate(page, context, page_info.has_linkedin_oauth)
        if not auth_result.success:
            return "login_required", f"Auth failed: {auth_result.error}"

        # Re-classify after auth
        await random_delay(2, 3)
        page_info = await classify_page(page)

        if page_info.page_type == "expired":
            return "expired", "Job expired after auth"
        if page_info.page_type == "success":
            return "already_applied", "Already applied (after auth)"

    # Step 5: If we're on a job listing page (not form yet), click Apply
    if page_info.page_type == "job_listing" and page_info.has_apply_button:
        nav_result = await click_apply_button(page, context)
        if nav_result.success:
            # If a new tab opened, switch to it
            if nav_result.data.get("new_tab"):
                new_url = nav_result.data.get("new_page_url", "")
                new_pages = [p for p in context.pages if p.url == new_url]
                if new_pages:
                    active_page = new_pages[-1]
                    await dismiss_overlays(active_page)
                    return await _handle_external_page(active_page, context, job, client)

            # Same tab — re-classify
            await random_delay(2, 3)
            page_info = await classify_page(page)
        else:
            return "no_apply_button", f"Cannot find Apply button: {nav_result.error}"

    # Step 6: Route to form agent based on platform
    return await _route_to_form_agent(page, context, job, client, page_info)


async def _handle_linkedin_page(page: Page, context: BrowserContext, job: dict,
                                  client, page_info: PageClassification) -> tuple[str, str]:
    """Handle a LinkedIn job page — Easy Apply or external redirect."""

    # Check if LinkedIn needs re-login
    if page_info.page_type == "login" and page_info.platform == "linkedin":
        auth_result = await handle_linkedin_signin(page)
        if not auth_result.success:
            return "linkedin_login_wall", f"LinkedIn auth: {auth_result.error}"
        await random_delay(3, 5)
        page_info = await classify_page(page)

    # Check for job expiry on LinkedIn
    try:
        body = (await page.inner_text("body")).lower()
        if "no longer accepting" in body or "no longer available" in body:
            return "expired", "Job no longer available"
        if "/jobs/search" in page.url and "/jobs/view/" not in page.url:
            return "expired", "Redirected to search (job removed)"
    except Exception:
        pass

    # Easy Apply
    if page_info.has_easy_apply:
        print("    -> Easy Apply detected")
        try:
            result = await handle_easy_apply(page, job)
            if result == "applied":
                return "applied", "Easy Apply submitted"
            elif result == "expired":
                return "expired", "Job expired during Easy Apply"
            elif result == "external":
                pass  # Fall through to external handling
            else:
                return result, f"Easy Apply: {result}"
        except Exception as e:
            return "easy_apply_error", f"Easy Apply failed: {str(e)[:80]}"

    # External Apply button
    if page_info.has_apply_button:
        nav_result = await click_apply_button(page, context)
        if nav_result.success:
            if nav_result.data.get("new_tab"):
                # Switch to new tab
                new_url = nav_result.data.get("new_page_url", "")
                new_pages = [p for p in context.pages if p.url == new_url and p != page]
                if new_pages:
                    active_page = new_pages[-1]
                    await dismiss_overlays(active_page)
                    status, reason = await _handle_external_page(active_page, context, job, client)
                    # Close the external tab when done
                    try:
                        if not active_page.is_closed():
                            await active_page.close()
                    except Exception:
                        pass
                    return status, reason
            else:
                # Same tab — check if modal opened or we navigated away
                await random_delay(2, 3)
                if "linkedin.com" not in page.url:
                    return await _handle_external_page(page, context, job, client)

                # Check for Easy Apply modal
                try:
                    dialog = page.locator('[role="dialog"]')
                    if await dialog.is_visible(timeout=3000):
                        result = await handle_easy_apply(page, job)
                        if result == "applied":
                            return "applied", "Easy Apply submitted"
                        return result, f"Easy Apply modal: {result}"
                except Exception:
                    pass

    return "no_apply_button", "No Apply button found on LinkedIn page"


async def _handle_external_page(page: Page, context: BrowserContext, job: dict, client) -> tuple[str, str]:
    """Handle an external application page (after redirect from LinkedIn or direct URL).

    Uses a loop to handle multi-step navigation: listing → login → form.
    Max 3 iterations to prevent infinite loops.
    """
    for attempt in range(3):
        await dismiss_overlays(page)

        # Classify the current page
        page_info = await classify_page(page)

        # Terminal states — return immediately
        if page_info.page_type == "expired":
            return "expired", "Job expired on external page"
        if page_info.page_type == "success":
            return "already_applied", "Already applied on external platform"
        if page_info.page_type == "captcha":
            return "captcha", "CAPTCHA on external page"

        # Login required — authenticate and loop back
        if page_info.page_type == "login":
            auth_result = await authenticate(page, context, page_info.has_linkedin_oauth)
            if not auth_result.success:
                return "login_required", f"External auth failed: {auth_result.error}"
            await random_delay(2, 3)
            continue  # Re-classify after auth

        # Job listing — click Apply and loop back
        if page_info.page_type == "job_listing" and page_info.has_apply_button:
            nav_result = await click_apply_button(page, context)
            if nav_result.success:
                if nav_result.data.get("new_tab"):
                    new_url = nav_result.data.get("new_page_url", "")
                    new_pages = [p for p in context.pages if p.url == new_url and p != page]
                    if new_pages:
                        return await _handle_external_page(new_pages[-1], context, job, client)
                await random_delay(2, 3)
                continue  # Re-classify after clicking Apply
            else:
                return "no_apply_button", f"Apply button click failed: {nav_result.error}"

        # Form page or unknown — route to form agent
        break

    # Route to form agent
    return await _route_to_form_agent(page, context, job, client, page_info)


async def _route_to_form_agent(page: Page, context: BrowserContext, job: dict,
                                client, page_info: PageClassification) -> tuple[str, str]:
    """Route to the appropriate form-filling agent based on platform."""

    # Prepare cover letter
    cl_path = get_cover_letter_for_job(job)
    if not cl_path:
        try:
            cl_path = generate_cover_letter(client, job)
        except Exception:
            cl_path = ""

    resume_path = str(RESUME_PATH)
    set_current_job(job)

    platform = page_info.platform

    # Route to specialized agent
    if platform == "workday":
        print(f"    -> Routing to Workday agent")
        result = await workday_orchestrator_loop(page, job, resume_path, cl_path)

    elif platform == "greenhouse":
        print(f"    -> Routing to Greenhouse agent")
        result = await fill_greenhouse_form(page, job, resume_path, cl_path)

    elif platform == "lever":
        print(f"    -> Routing to Lever agent")
        result = await fill_lever_form(page, job, resume_path, cl_path)

    else:
        # Generic: SmartRecruiters, iCIMS, Eightfold, Reed, custom portals
        print(f"    -> Routing to Generic form agent ({platform})")
        result = await generic_form_loop(page, job, resume_path, cl_path)

    # Convert AgentResult to (status, reason) tuple
    if result.success:
        return "applied", result.data.get("reason", f"Submitted via {platform} agent")
    else:
        return result.status, result.error or result.data.get("reason", "Unknown failure")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_result(job: dict, status: str, reason: str):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "title", "company", "url", "platform", "status", "reason"])
        writer.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S"),
            job.get("title", "")[:100],
            job.get("company", "")[:50],
            job.get("url", "")[:200],
            job.get("source", "unknown"),
            status,
            reason[:200],
        ])


def save_failed_jobs(failed_jobs: list):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(FAILED_JOBS_FILE, "w") as f:
        json.dump(failed_jobs, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 70)
    print("  MULTI-AGENT JOB APPLICATION SYSTEM")
    print("  Agents: Classifier | Auth | Navigation | Workday | Greenhouse | Lever | Generic")
    print("=" * 70, flush=True)

    # Load jobs
    if not RESULTS_FILE.exists():
        print(f"  ERROR: {RESULTS_FILE} not found")
        return

    jobs = json.loads(RESULTS_FILE.read_text())
    print(f"\n  Total jobs: {len(jobs)}")

    # Load progress
    processed_urls = set()
    if PROGRESS_FILE.exists():
        progress_data = json.loads(PROGRESS_FILE.read_text())
        processed_urls = set(progress_data.get("processed", []))
        print(f"  Already processed: {len(processed_urls)}")

    remaining = [j for j in jobs if j.get("url", "") not in processed_urls]
    print(f"  Remaining: {len(remaining)}")

    if not remaining:
        print("  All jobs already processed!")
        return

    # Categorize for display
    linkedin_jobs = [j for j in remaining if "linkedin.com" in j.get("url", "")]
    workday_jobs = [j for j in remaining if "myworkdayjobs.com" in j.get("url", "")]
    greenhouse_jobs = [j for j in remaining if "greenhouse" in j.get("url", "")]
    lever_jobs = [j for j in remaining if "lever.co" in j.get("url", "")]
    other_jobs = [j for j in remaining
                  if j not in linkedin_jobs + workday_jobs + greenhouse_jobs + lever_jobs]

    print(f"\n  Breakdown:")
    print(f"    LinkedIn:    {len(linkedin_jobs)}")
    print(f"    Workday:     {len(workday_jobs)}")
    print(f"    Greenhouse:  {len(greenhouse_jobs)}")
    print(f"    Lever:       {len(lever_jobs)}")
    print(f"    Other:       {len(other_jobs)}")

    # Priority: LinkedIn Easy Apply first, then programmatic (Greenhouse/Lever),
    # then Workday, then others
    remaining = linkedin_jobs + greenhouse_jobs + lever_jobs + workday_jobs + other_jobs
    print(f"\n  Order: LinkedIn > Greenhouse > Lever > Workday > Other")

    # Start browser
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )

        context_options = {
            "viewport": {"width": 1366, "height": 768},
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "locale": "en-GB",
            "timezone_id": "Europe/London",
        }

        # Use stored LinkedIn session
        if STORAGE_FILE.exists():
            context_options["storage_state"] = str(STORAGE_FILE)
            print("\n  Loaded LinkedIn session")

        context = await browser.new_context(**context_options)
        page = await context.new_page()

        # Verify LinkedIn login
        print("  Checking LinkedIn login...")
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)

        if "login" in page.url.lower() or "signin" in page.url.lower() or "checkpoint" in page.url.lower():
            print("  Session expired — logging in...")
            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            await asyncio.sleep(3)

            username_selectors = ['#username', 'input[name="session_key"]', 'input[type="email"]']
            for sel in username_selectors:
                try:
                    if await page.locator(sel).is_visible(timeout=3000):
                        await page.fill(sel, LINKEDIN_EMAIL)
                        break
                except Exception:
                    continue

            pw_selectors = ['#password', 'input[name="session_password"]', 'input[type="password"]']
            for sel in pw_selectors:
                try:
                    if await page.locator(sel).is_visible(timeout=2000):
                        await page.fill(sel, LINKEDIN_PASSWORD)
                        break
                except Exception:
                    continue

            for sel in ['button[type="submit"]', 'button:has-text("Sign in")']:
                try:
                    if await page.locator(sel).is_visible(timeout=2000):
                        await page.locator(sel).click()
                        break
                except Exception:
                    continue

            await asyncio.sleep(5)

            if "checkpoint" in page.url.lower() or "challenge" in page.url.lower():
                print("  Verification required — waiting 60s for manual completion...")
                await asyncio.sleep(60)

            if "login" in page.url.lower() or "checkpoint" in page.url.lower():
                print("  ERROR: LinkedIn login failed")
                await browser.close()
                return

            # Save new session
            state = await context.storage_state()
            STORAGE_FILE.write_text(json.dumps(state))
            print("  Logged in and saved session!")
        else:
            print("  LinkedIn session valid!")

        # Get AI client
        client = get_client()

        # Stats
        stats = {"applied": 0, "login_required": 0, "failed": 0, "expired": 0, "skipped": 0, "recording": 0}
        failed_jobs_list = []
        recording_jobs_list = []

        print(f"\n  Starting applications...\n")

        for idx, job in enumerate(remaining):
            title = job.get("title", "Unknown")[:55]
            company = job.get("company", "")[:25]
            url = job.get("url", "")
            source = job.get("source", "unknown")

            print(f"\n  [{idx+1}/{len(remaining)}] {title} | {company} ({source})")

            # Use full timeout — Easy Apply is fast but external redirects need time
            # The Easy Apply handler itself is quick; external forms need 280s
            timeout = JOB_TIMEOUT

            try:
                status, reason = await asyncio.wait_for(
                    process_job(page, context, job, client),
                    timeout=timeout
                )

                if status == "applied":
                    stats["applied"] += 1
                    print(f"    APPLIED!")
                elif status == "already_applied":
                    stats["applied"] += 1
                    print(f"    -> Already applied")
                elif status in ("login_required", "linkedin_login_wall"):
                    stats["login_required"] += 1
                    failed_jobs_list.append({**job, "fail_reason": reason})
                    print(f"    -> Login needed: {reason[:60]}")
                elif status == "expired":
                    stats["expired"] += 1
                    print(f"    -> Expired")
                elif status == "captcha":
                    stats["skipped"] += 1
                    print(f"    -> CAPTCHA blocked")
                elif status == "needs_recording":
                    stats["recording"] += 1
                    recording_jobs_list.append({**job, "reason": reason})
                    print(f"    -> RECORDING REQUIRED (saved for later)")
                else:
                    stats["failed"] += 1
                    failed_jobs_list.append({**job, "fail_reason": reason})
                    print(f"    X {status}: {reason[:60]}")

                log_result(job, status, reason)

            except asyncio.TimeoutError:
                stats["failed"] += 1
                print(f"    X TIMEOUT ({timeout}s)")
                log_result(job, "timeout", f"Exceeded {timeout}s per-job limit")
                failed_jobs_list.append({**job, "fail_reason": "timeout"})
            except Exception as e:
                stats["failed"] += 1
                print(f"    X ERROR: {str(e)[:60]}")
                log_result(job, "error", str(e)[:200])
                failed_jobs_list.append({**job, "fail_reason": str(e)[:200]})

            # Mark as processed
            processed_urls.add(url)
            PROGRESS_FILE.write_text(json.dumps({"processed": list(processed_urls)}, indent=2))

            # Delay between jobs
            if "linkedin.com" in url:
                await asyncio.sleep(random.uniform(5, 10))
            else:
                await asyncio.sleep(random.uniform(8, 15))

            # Progress update & pause
            if (idx + 1) % PAUSE_EVERY_N == 0:
                print(f"\n  --- Progress: {idx+1}/{len(remaining)} ---")
                print(f"  Applied: {stats['applied']} | Login: {stats['login_required']} | "
                      f"Failed: {stats['failed']} | Expired: {stats['expired']}")
                print(f"  Pausing {PAUSE_DURATION}s...\n", flush=True)
                await asyncio.sleep(PAUSE_DURATION)

        # Final summary
        print(f"\n{'=' * 70}")
        print(f"  COMPLETE — Multi-Agent Results")
        print(f"  Applied:        {stats['applied']}")
        print(f"  Login needed:   {stats['login_required']}")
        print(f"  Failed:         {stats['failed']}")
        print(f"  Expired:        {stats['expired']}")
        print(f"  Skipped:        {stats['skipped']}")
        print(f"  Recording:      {stats['recording']}")
        print(f"  Log:            {LOG_FILE}")
        print(f"  Failed jobs:    {FAILED_JOBS_FILE}")
        if recording_jobs_list:
            print(f"  Recording jobs: {RECORDING_JOBS_FILE}")
        print(f"{'=' * 70}")

        # Save failed jobs for retry
        if failed_jobs_list:
            save_failed_jobs(failed_jobs_list)

        # Save recording jobs for manual handling
        if recording_jobs_list:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            with open(RECORDING_JOBS_FILE, "w") as f:
                json.dump(recording_jobs_list, f, indent=2)
            print(f"\n  {len(recording_jobs_list)} jobs need video/audio recording — saved to {RECORDING_JOBS_FILE}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
