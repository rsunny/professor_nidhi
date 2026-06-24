"""eFinancialCareers Apply — Applies to filtered eFC jobs using AI form filling.

Reads efc_jobs_filtered.json and applies to each job via the eFC platform.

Usage:
    python3 -u apply_efc_jobs.py
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

from config import DATA_DIR, OUTPUT_DIR, RESUME_PATH
from ai_navigator import get_client
from profile_tools import (
    FORM_TOOLS, execute_lookup,
    set_current_job, get_cover_letter_for_job,
)
from linkedin_apply import _execute_tool_call

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

EFC_FILTERED_FILE = DATA_DIR / "efc_jobs_filtered.json"
EFC_STORAGE_FILE = DATA_DIR / "efinancial_storage_state.json"
EFC_APPLY_LOG = OUTPUT_DIR / "efc_applications_log.csv"
EFC_APPLY_PROGRESS = DATA_DIR / "efc_apply_progress.json"

EMAIL = os.getenv("EFINANCE_EMAIL", "")
PASSWORD = os.getenv("EFINANCE_PASSWORD", "")


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def ensure_logged_in(page: Page) -> bool:
    """Check/perform eFC login."""
    await page.goto("https://www.efinancialcareers.co.uk/", wait_until="domcontentloaded")
    await asyncio.sleep(2)

    # Check if logged in by looking for profile/account indicators
    logged_in = await page.evaluate("""() => {
        const body = document.body.innerText.toLowerCase();
        return body.includes('my account') || body.includes('profile') ||
               document.querySelector('[class*="avatar"], [class*="profile-pic"]') !== null;
    }""")

    if logged_in:
        return True

    # Try login
    print("  Logging in to eFC...")
    await page.goto("https://www.efinancialcareers.co.uk/login", wait_until="domcontentloaded")
    await asyncio.sleep(3)

    if "/login" not in page.url.lower():
        print("  Already logged in!")
        return True

    try:
        await page.fill('#email', EMAIL)
        await asyncio.sleep(0.5)
        await page.fill('#password', PASSWORD)
        await asyncio.sleep(0.5)
        await page.click('button.submit, button[type="submit"]')
        await asyncio.sleep(5)

        if "/login" not in page.url.lower():
            print("  Logged in successfully!")
            # Save session
            state = await page.context.storage_state()
            EFC_STORAGE_FILE.write_text(json.dumps(state))
            return True
        else:
            print("  Login failed")
            return False
    except Exception as e:
        print(f"  Login error: {e}")
        return False


# ---------------------------------------------------------------------------
# Apply to individual job
# ---------------------------------------------------------------------------

async def apply_to_job(page: Page, job: dict, client) -> tuple[str, str]:
    """Navigate to job and apply."""
    url = job["url"]

    await page.goto(url, wait_until="domcontentloaded")
    await asyncio.sleep(random.uniform(2, 4))

    # Check for 404 or expired
    page_text = await page.inner_text("body")
    page_text_lower = page_text.lower()

    if "page not found" in page_text_lower or "404" in page_text[:200].lower():
        return "expired", "Job page not found (404)"

    if "this position has been filled" in page_text_lower or "no longer available" in page_text_lower:
        return "expired", "Position filled or no longer available"

    if "you have already applied" in page_text_lower or "already applied" in page_text_lower:
        return "already_applied", "Already applied"

    # Find Apply button
    apply_btn = page.locator(
        'button:has-text("Apply"), '
        'a:has-text("Apply"), '
        'button:has-text("Quick apply"), '
        'a:has-text("Quick apply"), '
        'button:has-text("Apply now"), '
        'a:has-text("Apply now"), '
        '[class*="apply-btn"], '
        '[class*="ApplyButton"], '
        '[data-gtm-trackable*="Apply"]'
    ).first

    try:
        if not await apply_btn.is_visible(timeout=5000):
            # Check for external apply link
            external = page.locator('a:has-text("Apply on company"), a:has-text("Apply on employer")').first
            try:
                if await external.is_visible(timeout=3000):
                    return "external", "Redirects to external company site"
            except Exception:
                pass
            return "no_apply_button", "Apply button not found"
    except Exception:
        return "no_apply_button", "Apply button not found"

    await apply_btn.click()
    await asyncio.sleep(random.uniform(2, 4))

    # Check for external redirect
    if "efinancialcareers" not in page.url.lower():
        return "external", f"Redirected to {page.url.split('/')[2]}"

    # Check if already applied (popup or message after clicking)
    try:
        page_text = await page.inner_text("body")
        if "already applied" in page_text.lower():
            return "already_applied", "Already applied"
        if "application submitted" in page_text.lower() or "thank you" in page_text.lower():
            return "applied", "Quick-applied successfully"
    except Exception:
        pass

    # Fill form with AI agent
    return await ai_fill_efc_form(page, job, client)


async def ai_fill_efc_form(page: Page, job: dict, client) -> tuple[str, str]:
    """Use AI to fill and submit eFC application form."""
    resume_path = str(RESUME_PATH)
    set_current_job(job)
    cl_path = get_cover_letter_for_job(job) or ""

    system_prompt = f"""You are filling a job application form on eFinancialCareers.

JOB: {job.get('title', 'Unknown')} at {job.get('company', 'Unknown')}

You have these tools:
- lookup_answer: Call this for EVERY question/field you encounter. It returns the correct answer.
- fill_field: Fill a text input
- select_option: Select from dropdown
- click_element: Click buttons, radios, checkboxes
- upload_file: Upload resume/cover letter
- done: Signal completion

WORKFLOW:
1. Look at the form elements on the page
2. For each empty field: call lookup_answer to get the answer, then fill_field/select_option
3. Upload CV/resume when you see a file upload
4. If there's a cover letter upload, upload it too
5. Click Submit/Send/Apply to submit the application
6. After submission, call done(status="applied")

RULES:
- Call lookup_answer BEFORE filling any field
- Skip fields that are already filled correctly
- If you see a login form, call done(status="login_required", reason="Login required")
- If the job says "already applied", call done(status="already_applied")
- If you see a success/thank you/submitted message, call done(status="applied")
- If you can't find a form (just job description), look for Apply/Quick Apply button
- Cover letter path: {cl_path or 'use generic'}
"""

    messages = []

    for step in range(25):
        try:
            page_content = await get_page_elements(page)
        except Exception:
            await asyncio.sleep(2)
            continue

        if not page_content.strip():
            await asyncio.sleep(2)
            continue

        messages.append({"role": "user", "content": f"Step {step + 1}. Current page elements:\n\n{page_content}"})

        try:
            response = client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1"),
                max_tokens=1024,
                system=system_prompt,
                messages=messages,
                tools=FORM_TOOLS,
            )
        except Exception as e:
            return "api_error", f"API call failed: {str(e)[:150]}"

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            tool_results = []
            done_status = None
            reason = ""

            for block in response.content:
                if block.type != "tool_use":
                    continue

                if block.name == "lookup_answer":
                    question = block.input.get("question", "")
                    field_type = block.input.get("field_type", "text")
                    options = block.input.get("options")
                    result_str = execute_lookup(question, field_type, options)
                    result_data = json.loads(result_str)
                    print(f"      Q: {question[:50]} -> {result_data.get('answer', '')[:30]}")
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result_str})
                elif block.name == "done":
                    done_status = block.input.get("status", "applied")
                    reason = block.input.get("reason", "")
                    print(f"      Done: {done_status} - {reason[:60]}")
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": f"done: {done_status}"})
                else:
                    result = await _execute_tool_call(page, block.name, block.input, resume_path, cl_path)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

            messages.append({"role": "user", "content": tool_results})

            if done_status:
                return done_status, reason

        elif response.stop_reason == "end_turn":
            try:
                body = await page.inner_text("body")
                if "thank" in body.lower() or "submitted" in body.lower() or "application sent" in body.lower():
                    return "applied", "Success page detected"
            except Exception:
                pass

    return "max_steps", "Reached max steps without completion"


async def get_page_elements(page: Page) -> str:
    """Get interactive elements from the current page."""
    elements = await page.evaluate("""() => {
        const results = [];
        let idx = 0;
        const selectors = [
            'input:not([type="hidden"])', 'textarea', 'select', 'button',
            'a[href]', '[role="button"]', '[role="checkbox"]', '[role="radio"]',
            '[role="combobox"]', 'label',
        ];
        const allElements = document.querySelectorAll(selectors.join(', '));
        for (const el of allElements) {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            const tag = el.tagName.toLowerCase();
            const type = el.getAttribute('type') || '';
            const name = el.getAttribute('name') || '';
            const id = el.getAttribute('id') || '';
            const ariaLabel = el.getAttribute('aria-label') || '';
            const placeholder = el.getAttribute('placeholder') || '';
            const value = el.value || '';
            const text = (el.innerText || el.textContent || '').trim().substring(0, 80);
            const href = el.getAttribute('href') || '';
            el.setAttribute('data-ai-idx', idx.toString());
            let desc = `[${idx}] <${tag}`;
            if (type) desc += ` type="${type}"`;
            if (name) desc += ` name="${name}"`;
            if (id) desc += ` id="${id}"`;
            if (ariaLabel) desc += ` aria-label="${ariaLabel}"`;
            if (placeholder) desc += ` placeholder="${placeholder}"`;
            if (value && tag !== 'button') desc += ` value="${value.substring(0, 50)}"`;
            if (text && tag !== 'input') desc += ` text="${text.substring(0, 60)}"`;
            if (href && tag === 'a') desc += ` href="${href.substring(0, 60)}"`;
            desc += '>';
            results.push(desc);
            idx++;
        }
        return results.join('\\n');
    }""")
    return elements


# ---------------------------------------------------------------------------
# Logging & Progress
# ---------------------------------------------------------------------------

def log_result(job: dict, status: str, reason: str = ""):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = EFC_APPLY_LOG.exists()
    with open(EFC_APPLY_LOG, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "title", "company", "url", "status", "reason"])
        writer.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S"),
            job.get("title", ""),
            job.get("company", ""),
            job.get("url", ""),
            status,
            reason[:200],
        ])


def load_progress() -> set:
    if EFC_APPLY_PROGRESS.exists():
        return set(json.loads(EFC_APPLY_PROGRESS.read_text()))
    return set()


def save_progress(processed: set):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EFC_APPLY_PROGRESS.write_text(json.dumps(list(processed)))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("  eFinancialCareers AUTO APPLY")
    print("=" * 60, flush=True)

    if not EMAIL or not PASSWORD:
        print("  ERROR: EFINANCE_EMAIL/EFINANCE_PASSWORD not set in .env")
        return

    # Load filtered jobs
    if not EFC_FILTERED_FILE.exists():
        print("  ERROR: efc_jobs_filtered.json not found. Run filter_efc_jobs.py first.")
        return

    jobs = json.loads(EFC_FILTERED_FILE.read_text())
    print(f"  Total jobs to apply: {len(jobs)}")

    # Load progress
    processed = load_progress()
    remaining = [j for j in jobs if j["url"] not in processed]
    print(f"  Already attempted: {len(processed)}")
    print(f"  Remaining: {len(remaining)}")

    if not remaining:
        print("  Nothing to process.")
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

        if EFC_STORAGE_FILE.exists():
            context_options["storage_state"] = str(EFC_STORAGE_FILE)
            print("  Loaded saved session")

        context = await browser.new_context(**context_options)
        page = await context.new_page()

        # Login
        if not await ensure_logged_in(page):
            await browser.close()
            return

        client = get_client()
        applied_count = 0
        skipped_count = 0
        failed_count = 0

        for idx, job in enumerate(remaining):
            title = job.get("title", "Unknown")
            print(f"\n  [{idx+1}/{len(remaining)}] {title}")
            print(f"    URL: {job['url']}")

            try:
                status, reason = await apply_to_job(page, job, client)

                if status == "applied":
                    applied_count += 1
                    print(f"    APPLIED!")
                elif status in ("already_applied", "expired"):
                    skipped_count += 1
                    print(f"    Skipped: {status}")
                elif status == "external":
                    failed_count += 1
                    print(f"    External: {reason[:80]}")
                else:
                    failed_count += 1
                    print(f"    {status}: {reason[:80]}")

                log_result(job, status, reason)

            except Exception as e:
                failed_count += 1
                print(f"    ERROR: {str(e)[:100]}")
                log_result(job, "error", str(e)[:200])

            # Mark as processed
            processed.add(job["url"])
            save_progress(processed)

            # Delay between jobs
            await asyncio.sleep(random.uniform(3, 6))

            if (idx + 1) % 5 == 0:
                print(f"\n  --- Progress: {idx+1}/{len(remaining)} | Applied: {applied_count} | Skipped: {skipped_count} | Failed: {failed_count} ---", flush=True)

        print(f"\n{'=' * 60}")
        print(f"  COMPLETE")
        print(f"  Applied: {applied_count}")
        print(f"  Skipped: {skipped_count}")
        print(f"  Failed: {failed_count}")
        print(f"{'=' * 60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
