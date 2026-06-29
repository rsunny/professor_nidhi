"""Batch LinkedIn Easy Apply — Fast processing of all LinkedIn Easy Apply jobs.

Uses the existing Easy Apply AI form filler but with reduced delays.
Processes all LinkedIn URLs from careers_scrape_results.json.

Usage:
    python3 -u batch_easy_apply.py
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

RESULTS_FILE = DATA_DIR / "careers_scrape_results.json"
STORAGE_FILE = DATA_DIR / "storage_state.json"
PROGRESS_FILE = DATA_DIR / "batch_easy_apply_progress.json"
LOG_FILE = OUTPUT_DIR / "batch_easy_apply_log.csv"
LOGIN_URLS_FILE = OUTPUT_DIR / "jobs_needing_login.txt"

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")


# ---------------------------------------------------------------------------
# Fast Easy Apply flow
# ---------------------------------------------------------------------------

async def fast_easy_apply(page: Page, job: dict, client) -> tuple[str, str]:
    """Fill Easy Apply form with minimal delays. Returns (status, reason)."""
    resume_path = str(RESUME_PATH)
    cl_path = get_cover_letter_for_job(job) or ""
    set_current_job(job)

    system_prompt = build_tool_system_prompt(job, resume_path, cl_path)
    messages = []
    form_model = os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1")

    for step in range(20):
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
                model=form_model,
                max_tokens=1024,
                system=system_prompt,
                messages=messages[-6:],  # Keep context lean
                tools=FORM_TOOLS,
            )
        except Exception as e:
            return "api_error", f"API call failed: {str(e)[:100]}"

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

    return "max_steps", "Reached 20 steps without completion"


async def process_linkedin_job(page: Page, job: dict, client) -> tuple[str, str]:
    """Process a single LinkedIn job: navigate, click Easy Apply, fill form."""
    url = job.get("url", "")

    # Convert uk.linkedin.com to www.linkedin.com (logged-in version)
    url = url.replace("uk.linkedin.com", "www.linkedin.com")

    # Navigate to job page
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    except Exception as e:
        return "navigation_error", f"Failed to load: {str(e)[:80]}"

    await asyncio.sleep(random.uniform(5, 8))  # LinkedIn needs time to render SPA

    # Check if we got redirected to login
    if "login" in page.url.lower() or "signin" in page.url.lower():
        return "session_expired", "LinkedIn session expired"

    # Check if job exists
    try:
        body = await page.inner_text("body")
        body_lower = body.lower()
        if "page not found" in body_lower or "uh oh" in body_lower:
            return "expired", "Job page not found"
        if "no longer accepting" in body_lower or "job is closed" in body_lower:
            return "expired", "Job is closed"
    except Exception:
        pass

    # Find Easy Apply button (LinkedIn uses <a> or <button> with hashed classes)
    easy_apply_btn = None
    is_easy_apply = False
    selectors = [
        '[aria-label*="Easy Apply to"]',
        'a:has-text("Easy Apply")',
        'button:has-text("Easy Apply")',
        '.jobs-apply-button',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                cls = await btn.get_attribute("class") or ""
                if "filter" not in cls and "pill" not in cls and "search-reusables" not in cls:
                    easy_apply_btn = btn
                    is_easy_apply = True
                    break
        except Exception:
            continue

    if not is_easy_apply:
        return "no_apply_button", "No Easy Apply button found"

    # Click Easy Apply
    await easy_apply_btn.click()
    await asyncio.sleep(random.uniform(2, 3))

    # Wait for modal
    modal_sel = '[role="dialog"]:visible, .artdeco-modal:visible, .jobs-easy-apply-modal'
    try:
        await page.wait_for_selector(modal_sel, timeout=8000)
    except Exception:
        # Try clicking again
        try:
            await easy_apply_btn.click()
            await asyncio.sleep(3)
            await page.wait_for_selector(modal_sel, timeout=5000)
        except Exception:
            return "no_modal", "Easy Apply modal didn't open"

    # Fill the form with AI
    status, reason = await fast_easy_apply(page, job, client)

    # If scanned (ready to submit), auto-submit
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
                return "applied", "Successfully submitted"
        except Exception:
            pass
        return "scanned", "Form filled but submit button not found"

    # Close modal if we failed
    if status not in ("applied",):
        try:
            close_btn = page.locator('button[aria-label="Dismiss"], button[aria-label="Close"]').first
            if await close_btn.is_visible(timeout=2000):
                await close_btn.click()
                await asyncio.sleep(1)
        except Exception:
            pass

    return status, reason


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
    print("  BATCH LINKEDIN EASY APPLY")
    print("  Processing all LinkedIn Easy Apply jobs from scrape results")
    print("=" * 70, flush=True)

    # Load jobs — only LinkedIn URLs
    if not RESULTS_FILE.exists():
        print("  ERROR: careers_scrape_results.json not found")
        return

    all_jobs = json.loads(RESULTS_FILE.read_text())
    linkedin_jobs = [j for j in all_jobs if "linkedin" in j.get("url", "")]
    print(f"\n  Total LinkedIn jobs: {len(linkedin_jobs)}")

    # Load progress
    processed_urls = set()
    if PROGRESS_FILE.exists():
        processed_urls = set(json.loads(PROGRESS_FILE.read_text()))
        print(f"  Already processed: {len(processed_urls)}")

    remaining = [j for j in linkedin_jobs if j.get("url", "") not in processed_urls]
    print(f"  Remaining: {len(remaining)}")

    if not remaining:
        print("  All LinkedIn jobs already processed!")
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
        print("  Verifying LinkedIn login...")
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)

        if "login" in page.url.lower() or "signin" in page.url.lower():
            print("  Session expired — logging in...")
            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # Fill login
            selectors = ['#username', 'input[name="session_key"]', 'input[type="email"]']
            filled = False
            for sel in selectors:
                try:
                    if await page.locator(sel).is_visible(timeout=3000):
                        await page.fill(sel, LINKEDIN_EMAIL)
                        filled = True
                        break
                except Exception:
                    continue

            if filled:
                pw_sels = ['#password', 'input[name="session_password"]', 'input[type="password"]']
                for sel in pw_sels:
                    try:
                        if await page.locator(sel).is_visible(timeout=2000):
                            await page.fill(sel, LINKEDIN_PASSWORD)
                            break
                    except Exception:
                        continue

                await page.locator('button[type="submit"]').click()
                await asyncio.sleep(5)

            if "checkpoint" in page.url.lower() or "challenge" in page.url.lower():
                print("  Verification needed — waiting 60s for manual completion...")
                await asyncio.sleep(60)

            if "login" in page.url.lower():
                print("  ERROR: Login failed")
                await browser.close()
                return

            # Save session
            state = await context.storage_state()
            STORAGE_FILE.write_text(json.dumps(state))
            print("  Logged in!")
        else:
            print("  LinkedIn session valid!")

        # Dismiss any overlays
        await dismiss_overlays(page)

        client = get_client()

        # Stats
        applied = 0
        failed = 0
        expired = 0
        external = 0

        print(f"\n  Starting Easy Apply batch ({len(remaining)} jobs)...\n")

        for idx, job in enumerate(remaining):
            title = job.get("title", "Unknown")[:50]
            company = job.get("company", "")[:25]
            url = job.get("url", "")

            print(f"  [{idx+1}/{len(remaining)}] {title} | {company}")

            try:
                status, reason = await asyncio.wait_for(
                    process_linkedin_job(page, job, client), timeout=120
                )

                if status == "applied":
                    applied += 1
                    print(f"    APPLIED!")
                elif status == "expired":
                    expired += 1
                    print(f"    Expired: {reason[:50]}")
                elif status == "external":
                    external += 1
                    # Log external URLs for later processing
                    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                    with open(LOGIN_URLS_FILE, "a") as f:
                        f.write(f"{url} | {title} | {company} | EXTERNAL\n")
                    print(f"    External apply")
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
                # Close any open modal
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
            processed_urls.add(url)
            PROGRESS_FILE.write_text(json.dumps(list(processed_urls)))

            # Rate limit: ~5 apps/hour = 12 min between apps
            # But for just opening + filling forms, 10-15s between jobs is OK
            await asyncio.sleep(random.uniform(8, 15))

            # Progress update every 10
            if (idx + 1) % 10 == 0:
                print(f"\n  --- Progress: {idx+1}/{len(remaining)} | Applied: {applied} | Expired: {expired} | External: {external} | Failed: {failed} ---\n", flush=True)

        print(f"\n{'=' * 70}")
        print(f"  COMPLETE")
        print(f"  Applied: {applied}")
        print(f"  Expired: {expired}")
        print(f"  External: {external}")
        print(f"  Failed: {failed}")
        print(f"  Log: {LOG_FILE}")
        print(f"{'=' * 70}")

        # Save final session
        try:
            state = await context.storage_state()
            STORAGE_FILE.write_text(json.dumps(state))
        except Exception:
            pass

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
