"""Reed Auto Apply — Uses pre-fetched data to only visit jobs with apply buttons.

Reads from data/reed_jobs_fetched.json (already fetched via HTTP).
For jobs that have apply buttons: AI checks relevance from cached description, then applies via browser.

Usage:
    python3 -u apply_reed_filtered.py
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

from playwright.async_api import async_playwright, Page
from config import STORAGE_STATE, DATA_DIR, OUTPUT_DIR, RESUME_PATH
from browser import create_browser_context
from ai_navigator import get_client
from profile_tools import (
    FORM_TOOLS, execute_lookup, build_tool_system_prompt,
    set_current_job, get_cover_letter_for_job,
)
from linkedin_apply import _execute_tool_call

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REED_FETCHED_FILE = DATA_DIR / "reed_jobs_fetched.json"
REED_LOG_FILE = OUTPUT_DIR / "reed_applications_log.csv"
REED_APPLY_PROGRESS_FILE = DATA_DIR / "reed_apply_progress.json"

EXCLUDE_KEYWORDS = [
    "senior manager", "director", "head of", "vp ", "vice president",
    "10+ years", "10 years", "15 years", "principal", "lead architect",
    "chief", "cto", "cfo", "partner",
]


# ---------------------------------------------------------------------------
# Relevance check (uses cached description from fetched data)
# ---------------------------------------------------------------------------

def check_relevance_with_ai(client, title: str, description: str) -> tuple[bool, str]:
    """Use AI to determine if a job is relevant for Nidhi."""
    prompt = f"""You are filtering jobs for Nidhi Shetty. She has:
- 5 years experience, 2.5 in financial services (Morgan Stanley Prime Brokerage)
- MSc Investment & Risk Finance (Distinction)
- Skills: trade settlement, reconciliation, middle office, Excel/VBA, Bloomberg, Python (beginner)
- Looking for: trade operations, middle office, settlement, reconciliation, finance analyst roles
- Location: London (already based there)
- Needs Skilled Worker visa sponsorship
- Open to ANY salary range

JOB TITLE: {title}

JOB DESCRIPTION (first 2000 chars):
{description[:2000]}

Is this job relevant? Consider:
1. Finance/operations/analyst area? (YES needed)
2. Appropriate seniority (entry to mid-level, NOT director/VP/10+ years)? (YES needed)
3. London-based or remote? (YES needed)
4. PERMANENT role (not short-term day-rate contract)? (YES needed)

DO NOT skip based on salary.
Reply with EXACTLY one line: RELEVANT: <reason> or SKIP: <reason>"""

    try:
        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1"),
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response.content[0].text.strip()
        if answer.startswith("RELEVANT"):
            return True, answer
        return False, answer
    except Exception as e:
        return True, f"AI check failed ({e}), defaulting to relevant"


# ---------------------------------------------------------------------------
# Apply via browser
# ---------------------------------------------------------------------------

async def apply_to_job(page: Page, job: dict, client) -> tuple[str, str]:
    """Navigate to job and apply using AI agent. Returns (status, reason)."""
    job_url = job["url"]

    await page.goto(job_url, wait_until="domcontentloaded")
    await asyncio.sleep(random.uniform(1.5, 3))

    # Check login redirect
    if "login" in page.url.lower() or "sign-in" in page.url.lower():
        return "login_required", f"Redirected to login at {page.url.split('/')[2]}"

    # Check page state
    try:
        page_text = (await page.inner_text("body")).lower()
    except Exception:
        page_text = ""

    if "you have already applied" in page_text or "already applied" in page_text:
        return "already_applied", "Already applied to this job"

    if "this job has expired" in page_text or "no longer available" in page_text:
        return "expired", "Job listing has expired"

    # AI relevance check (inline — uses page text as description)
    is_relevant, reason = check_relevance_with_ai(client, job.get("title", ""), page_text[:3000])
    if not is_relevant:
        print(f"      SKIP: {reason[:80]}")
        return "skipped_irrelevant", reason

    print(f"      RELEVANT: {reason[:80]}")

    # Find and click Apply button
    apply_btn = page.locator(
        'a:has-text("Apply for this job"), '
        'a:has-text("Apply now"), '
        'button:has-text("Apply for this job"), '
        'button:has-text("Apply now"), '
        'a.apply-button, '
        'button.apply-button'
    ).first

    try:
        if not await apply_btn.is_visible(timeout=5000):
            apply_btn = page.locator('button:has-text("Apply"), a.btn:has-text("Apply")').first
            if not await apply_btn.is_visible(timeout=3000):
                return "no_apply_button", "Apply button not found on page"
    except Exception:
        return "no_apply_button", "Apply button not found on page"

    await apply_btn.click()
    await asyncio.sleep(random.uniform(1.5, 3))

    # Check if redirected to external site or login
    current_url = page.url.lower()
    if "reed.co.uk" not in current_url:
        domain = current_url.split("/")[2] if "/" in current_url else "unknown"
        return "external_redirect", f"Redirects to {domain}"

    if "signin" in current_url or "login" in current_url or "secure.reed" in current_url:
        return "login_required", f"Login required at {current_url.split('/')[2]}"

    # Fill form with AI agent
    return await ai_fill_reed_form(page, job, client)


async def ai_fill_reed_form(page: Page, job: dict, client) -> tuple[str, str]:
    """Use AI tool-based agent to fill and submit Reed application form."""
    resume_path = str(RESUME_PATH)
    set_current_job(job)
    cl_path = get_cover_letter_for_job(job) or ""

    system_prompt = f"""You are filling a job application form on Reed.co.uk.

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
- If you see a success/thank you message, call done(status="applied")
- If you see "Apply on external site" or "Complete on employer's website", call done(status="external_redirect", reason="Redirects to [domain from link]"). Do NOT click it.
- Cover letter path: {cl_path or 'use generic'}
"""

    messages = []

    for step in range(30):
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
    """Log application result to CSV."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = REED_LOG_FILE.exists()
    with open(REED_LOG_FILE, "a", newline="") as f:
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
    """Load already-applied URLs."""
    if REED_APPLY_PROGRESS_FILE.exists():
        return set(json.loads(REED_APPLY_PROGRESS_FILE.read_text()))
    return set()


def save_progress(processed: set):
    """Save processed URLs."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REED_APPLY_PROGRESS_FILE.write_text(json.dumps(list(processed)))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("  REED AUTO APPLY (Filtered — Apply Button Jobs Only)")
    print("=" * 60, flush=True)

    # Load pre-fetched data
    fetched = json.loads(REED_FETCHED_FILE.read_text())
    has_apply = [j for j in fetched if j.get("has_apply_button")]
    print(f"  Total fetched: {len(fetched)}")
    print(f"  With apply button: {len(has_apply)}")

    # Quick title filter
    def title_ok(title):
        t = title.lower()
        return not any(kw in t for kw in EXCLUDE_KEYWORDS)

    candidates = [j for j in has_apply if title_ok(j.get("title", ""))]
    print(f"  After title filter: {len(candidates)}")

    # Load progress (skip already attempted)
    processed = load_progress()
    remaining = [j for j in candidates if j["url"] not in processed]
    print(f"  Already attempted: {len(processed)}")
    print(f"  Remaining to process: {len(remaining)}")

    if not remaining:
        print("  Nothing to process.")
        return

    client = get_client()

    # Apply via browser (AI relevance check happens inline when page is loaded)
    print(f"\n  Applying to {len(remaining)} jobs via browser (AI filters inline)...\n", flush=True)

    async with async_playwright() as playwright:
        browser, context = await create_browser_context(playwright)
        page = await context.new_page()

        # Navigate to Reed to verify login
        await page.goto("https://www.reed.co.uk/", wait_until="domcontentloaded")
        await asyncio.sleep(2)
        if "signin" in page.url.lower():
            print("  WARNING: Not logged into Reed. Please login first.")
            await browser.close()
            return
        print("  Logged into Reed!", flush=True)

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
                elif status in ("skipped_irrelevant", "already_applied", "expired"):
                    skipped_count += 1
                    print(f"    Skipped: {status}")
                elif status == "external_redirect":
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
            await asyncio.sleep(random.uniform(3, 5))

            if (idx + 1) % 10 == 0:
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
