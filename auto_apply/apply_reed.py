"""Reed.co.uk Auto Apply — Opens each job, checks relevance via AI, applies if relevant.

Usage:
    python3 -u apply_reed.py
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

from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

from config import STORAGE_STATE, DATA_DIR, OUTPUT_DIR, RESUME_PATH
from browser import create_browser_context
from ai_navigator import get_client
from profile_tools import (
    FORM_TOOLS, execute_lookup, build_tool_system_prompt,
    set_current_job, get_cover_letter_for_job,
)
from linkedin_apply import _execute_tool_call
from humanizer import random_delay

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REED_JOBS_FILE = DATA_DIR / "jobs_reed_raw.json"
REED_LOG_FILE = OUTPUT_DIR / "reed_applications_log.csv"
REED_PROGRESS_FILE = DATA_DIR / "reed_progress.json"

RELEVANCE_KEYWORDS = [
    "trade", "settlement", "reconciliation", "middle office", "operations analyst",
    "finance analyst", "financial analyst", "risk analyst", "prime brokerage",
    "trade support", "post-trade", "clearing", "custody", "fund accounting",
    "investment operations", "asset servicing", "corporate actions",
    "financial operations", "treasury", "payments", "derivatives",
]

EXCLUDE_KEYWORDS = [
    "senior manager", "director", "head of", "vp ", "vice president",
    "10+ years", "10 years", "15 years", "principal", "lead architect",
    "chief", "cto", "cfo", "partner",
]


# ---------------------------------------------------------------------------
# Relevance check
# ---------------------------------------------------------------------------

def quick_title_filter(title: str) -> bool:
    """Fast pre-filter based on title alone. Returns True if potentially relevant."""
    t = title.lower()
    # Exclude senior/director level
    for kw in EXCLUDE_KEYWORDS:
        if kw in t:
            return False
    # Must contain at least one relevant keyword
    for kw in RELEVANCE_KEYWORDS:
        if kw in t:
            return True
    # Also allow generic analyst/finance roles
    if any(x in t for x in ["analyst", "finance", "operations", "banking"]):
        return True
    return False


async def check_relevance_with_ai(client, title: str, description: str) -> tuple[bool, str]:
    """Use AI to determine if a job is relevant for Nidhi. Returns (is_relevant, reason)."""
    prompt = f"""You are helping filter jobs for Nidhi Shetty. She has:
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

Is this job relevant for Nidhi? Consider:
1. Is it in finance/operations/analyst area? (YES needed)
2. Is the seniority appropriate (entry to mid-level, NOT director/VP/10+ years)? (YES needed)
3. Is it London-based or remote? (YES needed)
4. Is it a PERMANENT role (not a short-term contract paid by day rate)? (YES needed)

DO NOT skip based on salary. Apply to all salary ranges.
Only skip if it's clearly the wrong field (IT, marketing, construction etc), wrong seniority (VP/Director/10+ yrs), wrong location, or a short-term contract.

Reply with EXACTLY one line:
RELEVANT: <reason>
or
SKIP: <reason>"""

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
        # On error, default to applying (conservative)
        return True, f"AI check failed ({e}), defaulting to relevant"


# ---------------------------------------------------------------------------
# Reed application via AI agent
# ---------------------------------------------------------------------------

async def apply_to_reed_job(page: Page, job: dict, client) -> tuple[str, str]:
    """Navigate to Reed job, login if needed, and apply using AI agent.
    Returns (status, reason) tuple."""
    job_url = job["url"]

    # Navigate to job page
    await page.goto(job_url, wait_until="domcontentloaded")
    await asyncio.sleep(random.uniform(1.5, 3))

    # Check if we need to login
    if "login" in page.url.lower() or "sign-in" in page.url.lower():
        await reed_login(page)
        await page.goto(job_url, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(1.5, 3))

    # Get job description for relevance check
    try:
        description = await page.inner_text("body")
    except Exception:
        description = ""

    # Check if already applied
    page_text = description.lower()
    if "you have already applied" in page_text or "already applied" in page_text:
        return "already_applied", "Already applied to this job"

    # Check if job is expired
    if "this job has expired" in page_text or "no longer available" in page_text:
        return "expired", "Job listing has expired or is no longer available"

    # AI relevance check
    is_relevant, reason = await check_relevance_with_ai(client, job.get("title", ""), description)
    if not is_relevant:
        print(f"      SKIP: {reason[:80]}")
        return "skipped_irrelevant", reason

    print(f"      RELEVANT: {reason[:80]}")

    # Check for external apply links first (redirects to employer's site)
    external_link = page.locator(
        'a:has-text("Apply on company website"), '
        'a:has-text("Apply on employer"), '
        'a:has-text("Apply on external"), '
        'a:has-text("Complete on employer"), '
        'a:has-text("Apply on company")'
    ).first

    try:
        if await external_link.is_visible(timeout=2000):
            href = await external_link.get_attribute("href") or ""
            # Extract domain from href
            domain = ""
            if href.startswith("http"):
                domain = href.split("/")[2] if "/" in href else href
            elif "redirect" in href.lower():
                domain = "external (via reed redirect)"
            else:
                domain = "unknown external site"
            return "external_redirect", f"Redirects to {domain}"
    except Exception:
        pass

    # Find and click Apply button (Reed direct apply)
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
            # Try broader selector but exclude external links
            apply_btn = page.locator('button:has-text("Apply"), a.btn:has-text("Apply")').first
            if not await apply_btn.is_visible(timeout=3000):
                return "no_apply_button", "No direct Apply button found — may be external-only listing"
    except Exception:
        return "no_apply_button", "No direct Apply button found — may be external-only listing"

    await apply_btn.click()
    await asyncio.sleep(random.uniform(1.5, 3))

    # Check if we landed on a login/sign-in page
    await asyncio.sleep(1)
    current_url = page.url.lower()
    page_body = ""
    try:
        page_body = (await page.inner_text("body")).lower()
    except Exception:
        pass

    if "signin" in current_url or "login" in current_url or "sign in to apply" in page_body or "secure.reed" in current_url:
        # Handle Reed application login
        logged_in = await reed_apply_login(page)
        if not logged_in:
            return "login_required", f"Login required at {current_url.split('/')[2] if '/' in current_url else 'unknown'}"
        await asyncio.sleep(random.uniform(1.5, 3))

    # Now we're on the application form — use AI agent to fill it
    return await ai_fill_reed_form(page, job, client)


async def ai_fill_reed_form(page: Page, job: dict, client) -> tuple[str, str]:
    """Use AI tool-based agent to fill and submit Reed application form.
    Returns (status, reason)."""
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
- If you see a login form, call done(status="login_required", reason="Login required at [domain]")
- If the job says "already applied", call done(status="already_applied")
- If you see a success/thank you message, call done(status="applied")
- If you see "Apply on external site" or "Complete on employer's website" — this means Reed is redirecting to an external portal. Call done(status="external_redirect", reason="Redirects to [domain name from the link]"). Do NOT click it.
- Cover letter path: {cl_path or 'use generic'}
"""

    messages = []
    max_steps = 30

    for step in range(max_steps):
        try:
            # For Reed, read the full page (not dialog-scoped)
            page_content = await get_page_elements(page)
        except Exception:
            await asyncio.sleep(2)
            continue

        if not page_content.strip():
            await asyncio.sleep(2)
            continue

        user_msg = f"Step {step + 1}. Current page elements:\n\n{page_content}"
        messages.append({"role": "user", "content": user_msg})

        try:
            response = client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1"),
                max_tokens=1024,
                system=system_prompt,
                messages=messages,
                tools=FORM_TOOLS,
            )
        except Exception as e:
            print(f"      API error: {e}")
            return "api_error", f"API call failed: {str(e)[:150]}"

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            tool_results = []
            done_status = None

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
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })
                elif block.name == "done":
                    done_status = block.input.get("status", "applied")
                    reason = block.input.get("reason", "")
                    print(f"      Done: {done_status} - {reason[:60]}")
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
                return done_status, reason

        elif response.stop_reason == "end_turn":
            # Check if page shows success
            try:
                body = await page.inner_text("body")
                if "thank" in body.lower() or "submitted" in body.lower() or "application sent" in body.lower():
                    return "applied", "Success page detected"
            except Exception:
                pass

    return "max_steps", "Reached max steps without completion"


async def get_page_elements(page: Page) -> str:
    """Get interactive elements from the current Reed page (full page, not dialog-scoped)."""
    elements = await page.evaluate("""() => {
        const results = [];
        let idx = 0;

        // Get all interactive elements
        const selectors = [
            'input:not([type="hidden"])',
            'textarea',
            'select',
            'button',
            'a[href]',
            '[role="button"]',
            '[role="checkbox"]',
            '[role="radio"]',
            '[role="combobox"]',
            'label',
        ];

        const allElements = document.querySelectorAll(selectors.join(', '));

        for (const el of allElements) {
            // Skip invisible elements
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

            // Set data-ai-idx for later interaction
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
# Reed login
# ---------------------------------------------------------------------------

async def reed_login(page: Page):
    """Login to Reed.co.uk — handles passwordless OTP flow.

    Reed uses email + verification code (sent to email).
    We enter the email, then wait for the user to enter the code manually,
    OR check if already logged in via saved cookies.
    """
    from dotenv import load_dotenv
    load_dotenv()

    email = os.getenv("REED_EMAIL", "")

    # First check if we're already logged in
    await page.goto("https://www.reed.co.uk/", wait_until="domcontentloaded")
    await asyncio.sleep(3)

    if "signin" not in page.url.lower() and "login" not in page.url.lower():
        print("    Already logged into Reed!")
        return

    print("    Logging into Reed.co.uk (passwordless flow)...")

    # Navigate to login page
    await page.goto("https://www.reed.co.uk/account/signin", wait_until="domcontentloaded")
    await asyncio.sleep(random.uniform(2, 4))

    # Fill email
    email_field = page.locator('input[name="email"], input[type="email"], #email, input[id*="email"]').first
    try:
        if await email_field.is_visible(timeout=5000):
            await email_field.fill(email)
            await asyncio.sleep(1)

            # Click continue/send code
            continue_btn = page.locator(
                'button:has-text("Continue"), '
                'button:has-text("Send"), '
                'button:has-text("Get code"), '
                'button[type="submit"]'
            ).first
            if await continue_btn.is_visible(timeout=3000):
                await continue_btn.click()
                await asyncio.sleep(3)
    except Exception:
        pass

    # Now wait for user to enter the verification code
    print("    ⚠️  Reed sent a verification code to your email.")
    print("    ⚠️  Please enter the code in the browser window.")
    print("    ⚠️  Waiting up to 120 seconds for login to complete...")

    # Poll for login completion
    for _ in range(60):
        await asyncio.sleep(2)
        current_url = page.url.lower()
        if "signin" not in current_url and "login" not in current_url and "verify" not in current_url:
            print("    Logged in to Reed!")
            # Save cookies for next time
            reed_state = DATA_DIR / "reed_storage_state.json"
            await page.context.storage_state(path=str(reed_state))
            return

    print("    WARNING: Login might not have completed. Continuing anyway...")


async def reed_apply_login(page: Page) -> bool:
    """Handle Reed's application-specific login (email + password or OTP).

    Returns True if login succeeds.
    """
    from dotenv import load_dotenv
    load_dotenv()

    email = os.getenv("REED_EMAIL", "")
    password = os.getenv("REED_PASSWORD", "")

    print("    Handling Reed application login...")

    # Try to fill email field
    email_field = page.locator(
        'input[name="email"], input[type="email"], input[id*="email"], '
        'input[name="Email"], input[placeholder*="email"]'
    ).first

    try:
        if await email_field.is_visible(timeout=5000):
            await email_field.fill(email)
            await asyncio.sleep(1)
    except Exception:
        pass

    # Try to fill password field (if available — Reed sometimes still has password login)
    pass_field = page.locator(
        'input[name="password"], input[type="password"], input[id*="password"], '
        'input[name="Password"]'
    ).first

    try:
        if await pass_field.is_visible(timeout=3000):
            await pass_field.fill(password)
            await asyncio.sleep(1)

            # Click submit/sign in
            submit_btn = page.locator(
                'button[type="submit"], button:has-text("Sign in"), '
                'button:has-text("Log in"), input[type="submit"]'
            ).first
            if await submit_btn.is_visible(timeout=3000):
                await submit_btn.click()
                await asyncio.sleep(5)

                # Check if login succeeded
                if "signin" not in page.url.lower() and "login" not in page.url.lower():
                    print("    Logged in via password!")
                    return True
    except Exception:
        pass

    # If no password field, try clicking continue/send code for OTP flow
    continue_btn = page.locator(
        'button:has-text("Continue"), button:has-text("Send"), '
        'button:has-text("Get code"), button[type="submit"]'
    ).first

    try:
        if await continue_btn.is_visible(timeout=3000):
            await continue_btn.click()
            await asyncio.sleep(3)
    except Exception:
        pass

    # Wait for user to enter OTP
    print("    ⚠️  Reed requires verification code. Check your email and enter it in the browser.")
    print("    ⚠️  Waiting up to 90 seconds...")

    for _ in range(45):
        await asyncio.sleep(2)
        current_url = page.url.lower()
        if "signin" not in current_url and "login" not in current_url and "verify" not in current_url:
            print("    Login successful!")
            return True

    print("    Login timed out.")
    return False


# ---------------------------------------------------------------------------
# Logging
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
    """Load set of already-processed Reed job URLs."""
    if REED_PROGRESS_FILE.exists():
        with open(REED_PROGRESS_FILE) as f:
            return set(json.load(f))
    return set()


def save_progress(processed: set):
    """Save processed URLs."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(REED_PROGRESS_FILE, "w") as f:
        json.dump(list(processed), f)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("  REED.CO.UK AUTO APPLY")
    print("=" * 60, flush=True)

    # Load jobs
    with open(REED_JOBS_FILE) as f:
        all_jobs = json.load(f)

    # Quick title filter first (no AI cost)
    filtered_jobs = [j for j in all_jobs if quick_title_filter(j.get("title", ""))]
    print(f"  Total Reed jobs: {len(all_jobs)}")
    print(f"  After title filter: {len(filtered_jobs)}")

    # Load progress
    processed = load_progress()
    remaining = [j for j in filtered_jobs if j["url"] not in processed]
    print(f"  Already processed: {len(processed)}")
    print(f"  Remaining: {len(remaining)}")

    if not remaining:
        print("  Nothing to process.")
        return

    # Start browser
    async with async_playwright() as playwright:
        browser, context = await create_browser_context(playwright)
        page = await context.new_page()

        # Login to Reed
        await reed_login(page)

        client = get_client()
        applied_count = 0
        skipped_count = 0
        failed_count = 0

        for idx, job in enumerate(remaining):
            job_url = job["url"]
            title = job.get("title", "Unknown")
            job["id"] = idx  # Give it an ID for compatibility

            print(f"\n  [{idx+1}/{len(remaining)}] {title}")
            print(f"    URL: {job_url}")

            try:
                status, reason = await apply_to_reed_job(page, job, client)

                if status == "applied":
                    applied_count += 1
                    print(f"    APPLIED!")
                elif status in ("skipped_irrelevant", "already_applied", "expired"):
                    skipped_count += 1
                    print(f"    Skipped: {status}")
                elif status == "external_redirect":
                    failed_count += 1
                    print(f"    External redirect: {reason[:100]}")
                else:
                    failed_count += 1
                    print(f"    Failed: {status} — {reason[:100]}")

                log_result(job, status, reason)

            except Exception as e:
                failed_count += 1
                print(f"    ERROR: {str(e)[:100]}")
                log_result(job, "error", str(e)[:200])

            # Mark as processed regardless of result
            processed.add(job_url)
            save_progress(processed)

            # Delay between jobs (Reed crawl-delay is 3s, we use a bit more for safety)
            delay = random.uniform(3, 5)
            await asyncio.sleep(delay)

            # Progress update every 10 jobs
            if (idx + 1) % 10 == 0:
                print(f"\n  --- Progress: {idx+1}/{len(remaining)} | Applied: {applied_count} | Skipped: {skipped_count} | Failed: {failed_count} ---")

        print(f"\n{'=' * 60}")
        print(f"  REED AUTO APPLY COMPLETE")
        print(f"  Applied: {applied_count}")
        print(f"  Skipped: {skipped_count}")
        print(f"  Failed: {failed_count}")
        print(f"  Total processed: {len(processed)}")
        print(f"{'=' * 60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
