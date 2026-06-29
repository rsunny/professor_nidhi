"""Apply to ALL 284 scraped jobs via LinkedIn → External Platform flow.

Flow:
1. Open LinkedIn job URL (logged in)
2. Click Apply button (external redirect)
3. On external platform:
   - If no login: fill form, click forward, submit
   - If login required: log URL to file, move on

Usage:
    python3 -u apply_scraped_jobs.py
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
from playwright.async_api import async_playwright, Page

load_dotenv(Path(__file__).parent / ".env")

from config import DATA_DIR, OUTPUT_DIR, RESUME_PATH
from ai_navigator import get_client
from profile_tools import FORM_TOOLS, execute_lookup, set_current_job, get_cover_letter_for_job

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RESULTS_FILE = DATA_DIR / "careers_scrape_results.json"
STORAGE_FILE = DATA_DIR / "storage_state.json"
PROGRESS_FILE = DATA_DIR / "apply_scraped_progress.json"
LOG_FILE = OUTPUT_DIR / "scraped_applications_log.csv"
LOGIN_URLS_FILE = OUTPUT_DIR / "jobs_needing_login.txt"
COVER_LETTERS_DIR = OUTPUT_DIR / "cover_letters_generated"

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")


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


def generate_cover_letter(client, job: dict, desc: str) -> str:
    """Generate a curated cover letter PDF for this job."""
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
    except Exception as e:
        letter_text = f"Dear Hiring Manager,\n\nI am writing to apply for the {title} position at {company}. With 5 years of experience in financial operations including 2.5 years at Morgan Stanley Prime Brokerage, I am confident I can contribute to your team.\n\nYours sincerely,\nNidhi Shetty"
    
    # Save as PDF
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
# Login detection
# ---------------------------------------------------------------------------

async def detect_login_page(page: Page) -> bool:
    """Check if current page is a login/signup page."""
    try:
        result = await page.evaluate("""() => {
            // Check for visible password fields
            const pwFields = document.querySelectorAll('input[type="password"]');
            for (const f of pwFields) {
                const rect = f.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) return true;
            }
            // Check for common login indicators
            if (document.querySelector('[data-automation-id="signIn"], [data-automation-id="login"]')) return true;
            // Check for login-focused page (short body with sign in)
            const body = document.body.innerText.toLowerCase();
            if (body.length < 1000 && (body.includes('sign in') || body.includes('log in') || body.includes('create account'))) return true;
            // Check URL
            const url = window.location.href.toLowerCase();
            if (url.includes('/login') || url.includes('/signin') || url.includes('/sso') || url.includes('/auth')) return true;
            return false;
        }""")
        return result
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Form filling with AI
# ---------------------------------------------------------------------------

async def get_page_elements(page: Page) -> str:
    """Get visible interactive elements."""
    try:
        elements = await page.evaluate("""() => {
            const results = [];
            let idx = 0;
            const selectors = [
                'input:not([type="hidden"])', 'textarea', 'select', 'button',
                'a[href]', '[role="button"]', '[role="checkbox"]', '[role="radio"]',
                '[role="combobox"]', 'label', '[type="file"]',
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
                
                el.setAttribute('data-ai-idx', idx.toString());
                let desc = `[${idx}] <${tag}`;
                if (type) desc += ` type="${type}"`;
                if (name) desc += ` name="${name}"`;
                if (id) desc += ` id="${id}"`;
                if (ariaLabel) desc += ` aria-label="${ariaLabel}"`;
                if (placeholder) desc += ` placeholder="${placeholder}"`;
                if (value && tag !== 'button') desc += ` value="${value.substring(0, 50)}"`;
                if (text && tag !== 'input') desc += ` text="${text.substring(0, 60)}"`;
                desc += '>';
                results.push(desc);
                idx++;
            }
            return results.join('\\n');
        }""")
        return elements
    except Exception:
        return ""


async def execute_browser_action(page: Page, action_name: str, action_input: dict, 
                                  resume_path: str, cl_path: str) -> str:
    """Execute a browser action from AI tool call."""
    try:
        if action_name == "fill_field":
            idx = action_input.get("index", 0)
            value = action_input.get("value", "")
            el = page.locator(f'[data-ai-idx="{idx}"]')
            await el.click()
            await el.fill(value)
            return f"Filled field {idx} with '{value[:30]}'"
            
        elif action_name == "select_option":
            idx = action_input.get("index", 0)
            value = action_input.get("value", "")
            el = page.locator(f'[data-ai-idx="{idx}"]')
            try:
                await el.select_option(label=value, timeout=3000)
            except Exception:
                try:
                    await el.select_option(value=value, timeout=3000)
                except Exception:
                    await el.click()
                    await asyncio.sleep(0.5)
                    option = page.locator(f'option:has-text("{value}"), li:has-text("{value}"), [role="option"]:has-text("{value}")')
                    if await option.first.is_visible(timeout=2000):
                        await option.first.click()
            return f"Selected '{value[:30]}' in field {idx}"
            
        elif action_name == "click_element":
            idx = action_input.get("index", 0)
            el = page.locator(f'[data-ai-idx="{idx}"]')
            await el.click()
            await asyncio.sleep(random.uniform(2, 3))  # 2-3s after button click
            return f"Clicked element {idx}"
            
        elif action_name == "upload_file":
            idx = action_input.get("index", 0)
            file_type = action_input.get("file_type", "resume")
            file_path = cl_path if file_type == "cover_letter" else resume_path
            if not file_path or not Path(file_path).exists():
                file_path = resume_path
            el = page.locator(f'[data-ai-idx="{idx}"]')
            await el.set_input_files(file_path)
            return f"Uploaded {file_type}: {Path(file_path).name}"
            
        elif action_name == "done":
            return f"Done: {action_input.get('status', '')} - {action_input.get('reason', '')}"
            
    except Exception as e:
        return f"Error: {str(e)[:100]}"
    
    return "Unknown action"


async def ai_fill_external_form(page: Page, job: dict, client, resume_path: str, cl_path: str) -> tuple[str, str]:
    """Use AI to fill and submit an external application form."""
    set_current_job(job)
    
    system_prompt = f"""You are filling a job application form on an external careers site.

JOB: {job.get('title', 'Unknown')} at {job.get('company', 'Unknown')}

You have these tools:
- lookup_answer: Call this for EVERY question/field you encounter. It returns the correct answer.
- fill_field: Fill a text input (index, value)
- select_option: Select from dropdown (index, value)
- click_element: Click buttons, radios, checkboxes (index)
- upload_file: Upload resume or cover letter (index, file_type)
- done: Signal completion (status, reason)

WORKFLOW:
1. Look at the form elements on the page
2. For each empty field: call lookup_answer, then fill_field/select_option
3. Upload CV/resume when you see a file upload
4. If there's a cover letter upload, upload it too
5. Click Next/Continue/Submit to advance
6. After submission success, call done(status="applied")

RULES:
- Call lookup_answer BEFORE filling any field
- Skip fields that are already filled correctly
- If you see a login form (password field, sign in), call done(status="login_required")
- If you see "already applied" or "application submitted", call done(status="already_applied")
- If you see a success/thank you page, call done(status="applied")
- Keep clicking forward (Next, Continue, Submit, Apply) to advance through steps
- Resume path: {resume_path}
- Cover letter: {cl_path}
"""

    messages = []
    form_model = os.getenv("FORM_FILL_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

    for step in range(15):
        # Check for login page first
        if await detect_login_page(page):
            return "login_required", f"Login page detected at {page.url}"

        try:
            page_content = await get_page_elements(page)
        except Exception:
            await asyncio.sleep(2)
            continue

        if not page_content.strip():
            await asyncio.sleep(2)
            continue

        # Check for success indicators in body text
        try:
            body = await page.inner_text("body")
            body_lower = body.lower()
            if any(x in body_lower for x in ["thank you for applying", "application submitted",
                                              "application received", "successfully submitted"]):
                return "applied", "Success page detected"
        except Exception:
            pass

        messages.append({"role": "user", "content": f"Step {step + 1}. Current page URL: {page.url}\n\nPage elements:\n{page_content[:3000]}"})

        try:
            response = client.messages.create(
                model=form_model,
                max_tokens=1024,
                system=system_prompt,
                messages=messages[-4:],  # Keep context lean
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
                
                if block.name == "lookup_answer":
                    question = block.input.get("question", "")
                    field_type = block.input.get("field_type", "text")
                    options = block.input.get("options")
                    result_str = execute_lookup(question, field_type, options)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result_str})
                elif block.name == "done":
                    done_status = block.input.get("status", "applied")
                    done_reason = block.input.get("reason", "")
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": f"done: {done_status}"})
                else:
                    result = await execute_browser_action(page, block.name, block.input, resume_path, cl_path)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
            
            messages.append({"role": "user", "content": tool_results})
            
            if done_status:
                return done_status, done_reason
        
        elif response.stop_reason == "end_turn":
            # Check if we're on a success page
            try:
                body = await page.inner_text("body")
                if any(x in body.lower() for x in ["thank", "submitted", "received", "application sent"]):
                    return "applied", "Success detected after end_turn"
            except Exception:
                pass
    
    return "max_steps", "Reached 15 steps without completion"


# ---------------------------------------------------------------------------
# Greenhouse form filler (programmatic, no AI needed)
# ---------------------------------------------------------------------------

async def fill_greenhouse_form(page: Page, job: dict, client) -> tuple[str, str]:
    """Fill Greenhouse application form directly using known field structure."""
    from profile_tools import execute_lookup
    import json as _json

    # Standard Greenhouse field IDs and their values
    profile_answers = {
        "first_name": "Nidhi",
        "last_name": "Shetty",
        "email": "nidhishettyuk23@gmail.com",
        "phone": "+447438416662",
        "candidate-location": "London, UK",
    }

    # Fill standard text fields
    for field_id, value in profile_answers.items():
        try:
            el = page.locator(f'#{field_id}')
            if await el.is_visible(timeout=2000):
                await el.fill(value)
                await asyncio.sleep(0.3)
        except Exception:
            pass

    # Upload resume
    try:
        resume_input = page.locator('#resume, input[type="file"][id*="resume"], input[type="file"]').first
        if await resume_input.count() > 0:
            await resume_input.set_input_files(str(RESUME_PATH))
            await asyncio.sleep(1)
    except Exception:
        pass

    # Upload cover letter if there's a second file input
    try:
        cl_inputs = page.locator('input[type="file"]')
        count = await cl_inputs.count()
        if count > 1:
            cl_path = generate_cover_letter(client, job, "")
            await cl_inputs.nth(1).set_input_files(cl_path)
            await asyncio.sleep(1)
    except Exception:
        pass

    # Fill any remaining custom fields using profile_tools lookup
    try:
        custom_fields = await page.evaluate("""() => {
            const results = [];
            const inputs = document.querySelectorAll('input:not([type="hidden"]):not([type="file"]), textarea, select');
            for (const inp of inputs) {
                if (inp.value) continue;  // Already filled
                const rect = inp.getBoundingClientRect();
                if (rect.width === 0) continue;
                const label = inp.closest('label, .field, .form-field')?.innerText?.trim() || inp.placeholder || inp.name || inp.id || '';
                if (label && label.length > 2) {
                    results.push({id: inp.id, name: inp.name, label: label.substring(0, 100), tag: inp.tagName, type: inp.type || 'text'});
                }
            }
            return results;
        }""")

        for field in custom_fields[:10]:  # Max 10 custom fields
            label = field.get("label", "")
            field_id = field.get("id", "")
            if not label:
                continue
            # Use lookup_answer
            try:
                result_str = execute_lookup(label, field.get("type", "text"), None)
                result = _json.loads(result_str)
                answer = result.get("answer", "")
                if answer and answer != "UNKNOWN":
                    sel = f'#{field_id}' if field_id else f'[name="{field.get("name", "")}"]'
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=1000):
                        await el.fill(str(answer))
                        await asyncio.sleep(0.3)
            except Exception:
                pass
    except Exception:
        pass

    # Click Submit
    submit_btn = page.locator(
        'button:has-text("Submit"), button[type="submit"], '
        'input[type="submit"], button:has-text("Apply"), '
        '#submit_app, button:has-text("Submit application")'
    ).first
    try:
        if await submit_btn.is_visible(timeout=3000):
            await submit_btn.click()
            await asyncio.sleep(3)

            # Check for success
            try:
                body = await page.inner_text("body")
                body_lower = body.lower()
                if any(x in body_lower for x in ["thank", "submitted", "received", "application sent"]):
                    return "applied", "Greenhouse form submitted successfully"
                if "error" in body_lower or "required" in body_lower:
                    return "form_error", "Form has validation errors"
            except Exception:
                pass
            return "applied", "Greenhouse submit clicked"
    except Exception:
        pass

    return "no_submit", "Could not find submit button on Greenhouse"


# ---------------------------------------------------------------------------
# Direct application (Workday, Greenhouse, etc.)
# ---------------------------------------------------------------------------

async def process_direct_application(page: Page, job: dict, client) -> tuple[str, str]:
    """Apply directly to Workday/Greenhouse/other ATS without LinkedIn."""
    url = job.get("url", "")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        return "navigation_error", f"Failed to load: {str(e)[:80]}"

    await asyncio.sleep(3)

    # For Workday — almost always requires account creation/login
    if "myworkdayjobs.com" in url:
        # Click Apply button first
        apply_btn = page.locator(
            'a:has-text("Apply"), button:has-text("Apply"), '
            '[data-automation-id="jobPostingApplyButton"]'
        ).first
        try:
            if await apply_btn.is_visible(timeout=5000):
                await apply_btn.click()
                await asyncio.sleep(3)
        except Exception:
            pass
        # Check for login after clicking Apply
        if await detect_login_page(page):
            return "login_required", f"Login required at {page.url}"
        # Check URL for sign-in redirect
        if "login" in page.url.lower() or "signin" in page.url.lower() or "/createAccount" in page.url:
            return "login_required", f"Account creation required at {page.url}"
        # Try filling the form
        cl_path = generate_cover_letter(client, job, "")
        return await ai_fill_external_form(page, job, client, str(RESUME_PATH), cl_path)

    # For Greenhouse — fill the standard form directly (no AI needed for known fields)
    elif "greenhouse.io" in url:
        if await detect_login_page(page):
            return "login_required", f"Login required at {page.url}"
        return await fill_greenhouse_form(page, job, client)

    # Generic external site
    if await detect_login_page(page):
        return "login_required", f"Login required at {page.url}"

    cl_path = generate_cover_letter(client, job, "")
    return await ai_fill_external_form(page, job, client, str(RESUME_PATH), cl_path)


# ---------------------------------------------------------------------------
# Process one job
# ---------------------------------------------------------------------------

async def process_job(page: Page, job: dict, client) -> tuple[str, str]:
    """Process a single job: LinkedIn → click Apply → external form."""
    url = job.get("url", "")

    if not url:
        return "no_url", "No URL available"

    # Direct Workday/Greenhouse URLs — apply directly (no LinkedIn needed)
    if "myworkdayjobs.com" in url or "greenhouse.io" in url:
        return await process_direct_application(page, job, client)

    # Step 1: Go to LinkedIn job page
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        return "navigation_error", f"Failed to load: {str(e)[:80]}"

    await asyncio.sleep(random.uniform(2, 3))

    # Check if LinkedIn redirected us to login
    if "login" in page.url.lower() or "signin" in page.url.lower():
        # Try again with the page - sometimes LinkedIn requires auth for job viewing
        return "linkedin_login_wall", "LinkedIn requires login for this job"

    # Check if we're still on LinkedIn
    if "linkedin.com" not in page.url:
        # Already redirected externally
        if await detect_login_page(page):
            return "login_required", f"External login at {page.url}"
        # Try to fill the external form directly
        cl_path = generate_cover_letter(client, job, job.get("title", ""))
        return await ai_fill_external_form(page, job, client, str(RESUME_PATH), cl_path)

    # Check if job page loaded (not a 404 or expired)
    try:
        body_text = await page.inner_text("body")
        body_lower = body_text.lower()
        if "no longer accepting" in body_lower or "no longer available" in body_lower:
            return "expired", "Job no longer available"
        if "page not found" in body_lower:
            return "expired", "Job page not found"
    except Exception:
        pass

    # Step 2: Find the external apply link URL directly from the page
    # LinkedIn public job pages have the external URL in an <a> tag
    external_url = await page.evaluate("""() => {
        // Look for Apply links that point externally
        const links = document.querySelectorAll('a');
        for (const link of links) {
            const text = (link.innerText || '').trim().toLowerCase();
            const href = link.href || '';
            if ((text.includes('apply') || text === 'apply now' || text === 'apply on company website')
                && href && !href.includes('linkedin.com') && href.startsWith('http')) {
                return href;
            }
        }
        // Also check for tracking redirect links (LinkedIn wraps external URLs)
        for (const link of links) {
            const text = (link.innerText || '').trim().toLowerCase();
            const href = link.href || '';
            if (text.includes('apply') && href.includes('linkedin.com/redir')) {
                return href;
            }
        }
        return null;
    }""")

    if external_url:
        # Navigate directly to the external URL
        try:
            await page.goto(external_url, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(3)

        # Check where we ended up
        if await detect_login_page(page):
            return "login_required", f"Login required at {page.url}"

        # Check for Workday account creation
        if "myworkdayjobs.com" in page.url and ("login" in page.url.lower() or "createAccount" in page.url):
            return "login_required", f"Workday account required at {page.url}"

        # Fill the external form
        cl_path = generate_cover_letter(client, job, "")
        return await ai_fill_external_form(page, job, client, str(RESUME_PATH), cl_path)

    # No external URL found — try clicking the Apply button
    apply_selectors = [
        'a:has-text("Apply")',
        'button:has-text("Apply")',
        '[class*="apply-button"]',
        'a[class*="apply"]',
        'button[class*="apply"]',
        '.jobs-apply-button',
    ]

    apply_btn = None
    for sel in apply_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                apply_btn = btn
                break
        except Exception:
            continue

    if not apply_btn:
        await page.evaluate("window.scrollTo(0, 500)")
        await asyncio.sleep(2)
        for sel in apply_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    apply_btn = btn
                    break
            except Exception:
                continue

    if not apply_btn:
        return "no_apply_button", "Apply button not found on LinkedIn"

    # Click apply — may open new tab or redirect in same tab
    original_url = page.url
    try:
        async with page.context.expect_page(timeout=8000) as new_page_info:
            await apply_btn.click()
        new_page = await new_page_info.value
        await new_page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(3)

        if await detect_login_page(new_page):
            ext_url = new_page.url
            try:
                await new_page.close()
            except Exception:
                pass
            return "login_required", f"Login required at {ext_url}"

        cl_path = generate_cover_letter(client, job, "")
        status, reason = await ai_fill_external_form(new_page, job, client, str(RESUME_PATH), cl_path)
        try:
            await new_page.close()
        except Exception:
            pass
        return status, reason

    except Exception:
        # No new tab opened — check if same-tab redirect happened
        await asyncio.sleep(3)

        if page.url != original_url and "linkedin.com" not in page.url:
            if await detect_login_page(page):
                return "login_required", f"Login required at {page.url}"
            cl_path = generate_cover_letter(client, job, "")
            return await ai_fill_external_form(page, job, client, str(RESUME_PATH), cl_path)

        # Still on LinkedIn — might be Easy Apply
        try:
            dialog = page.locator('[role="dialog"]')
            if await dialog.is_visible(timeout=2000):
                return "easy_apply", "Easy Apply modal (needs separate handler)"
        except Exception:
            pass

        return "no_external_redirect", "Apply button didn't redirect to external site"


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


def log_login_url(job: dict, url: str):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOGIN_URLS_FILE, "a") as f:
        f.write(f"{url} | {job.get('title', '')} | {job.get('company', '')}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 70)
    print("  APPLY TO ALL 284 SCRAPED JOBS")
    print("  Flow: LinkedIn → Click Apply → External Platform → Submit")
    print("=" * 70, flush=True)
    
    # Load jobs
    if not RESULTS_FILE.exists():
        print("  ERROR: careers_scrape_results.json not found")
        return
    
    jobs = json.loads(RESULTS_FILE.read_text())
    print(f"\n  Total jobs: {len(jobs)}")
    
    # Load progress
    processed_urls = set()
    if PROGRESS_FILE.exists():
        processed_urls = set(json.loads(PROGRESS_FILE.read_text()))
        print(f"  Already processed: {len(processed_urls)}")
    
    remaining = [j for j in jobs if j.get("url", "") not in processed_urls]
    print(f"  Remaining: {len(remaining)}")
    
    if not remaining:
        print("  All jobs already processed!")
        return
    
    # Start browser
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        
        context_options = {
            "viewport": {"width": 1366, "height": 768},
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "locale": "en-GB",
            "timezone_id": "Europe/London",
        }
        
        # Use stored LinkedIn session
        if STORAGE_FILE.exists():
            context_options["storage_state"] = str(STORAGE_FILE)
            print("  Loaded LinkedIn session")
        
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

            # Try multiple selectors for username field
            username_selectors = ['#username', 'input[name="session_key"]', 'input[autocomplete="username"]', 'input[type="email"]']
            filled = False
            for sel in username_selectors:
                try:
                    if await page.locator(sel).is_visible(timeout=3000):
                        await page.fill(sel, LINKEDIN_EMAIL)
                        filled = True
                        break
                except Exception:
                    continue

            if not filled:
                print("  WARNING: Could not find login field — waiting 60s for manual login...")
                print("  Please log in manually in the browser window.")
                await asyncio.sleep(60)
            else:
                # Fill password
                pw_selectors = ['#password', 'input[name="session_password"]', 'input[type="password"]']
                for sel in pw_selectors:
                    try:
                        if await page.locator(sel).is_visible(timeout=2000):
                            await page.fill(sel, LINKEDIN_PASSWORD)
                            break
                    except Exception:
                        continue

                # Click submit
                submit_selectors = ['button[type="submit"]', 'button:has-text("Sign in")', 'button[data-litms-control-urn*="login-submit"]']
                for sel in submit_selectors:
                    try:
                        if await page.locator(sel).is_visible(timeout=2000):
                            await page.locator(sel).click()
                            break
                    except Exception:
                        continue

                await asyncio.sleep(5)

            # Check if verification/CAPTCHA appeared
            if "checkpoint" in page.url.lower() or "challenge" in page.url.lower():
                print("  Verification required — waiting 45s for manual completion...")
                await asyncio.sleep(45)

            if "login" in page.url.lower() or "checkpoint" in page.url.lower():
                print("  ERROR: LinkedIn login failed (may need manual verification)")
                print("  TIP: Delete data/storage_state.json and try again after manual login")
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
        applied = 0
        login_needed = 0
        failed = 0
        skipped = 0
        
        print(f"\n  Starting applications...\n")
        
        for idx, job in enumerate(remaining):
            title = job.get("title", "Unknown")[:55]
            company = job.get("company", "")[:25]
            url = job.get("url", "")
            
            print(f"  [{idx+1}/{len(remaining)}] {title} | {company}")
            
            try:
                status, reason = await asyncio.wait_for(
                    process_job(page, job, client), timeout=90
                )

                if status == "applied":
                    applied += 1
                    print(f"    APPLIED!")
                elif status == "login_required":
                    login_needed += 1
                    log_login_url(job, reason.replace("Login required at ", "").replace("External login at ", "").replace("Login page detected at ", ""))
                    print(f"    -> Login needed: {reason[:60]}")
                elif status in ("already_applied", "easy_apply"):
                    skipped += 1
                    print(f"    -> Skipped: {status}")
                else:
                    failed += 1
                    print(f"    X {status}: {reason[:60]}")

                log_result(job, status, reason)

            except asyncio.TimeoutError:
                failed += 1
                print(f"    X TIMEOUT (90s)")
                log_result(job, "timeout", "Exceeded 90s per-job limit")
            except Exception as e:
                failed += 1
                print(f"    X ERROR: {str(e)[:60]}")
                log_result(job, "error", str(e)[:200])
            
            # Mark as processed
            processed_urls.add(url)
            PROGRESS_FILE.write_text(json.dumps(list(processed_urls)))
            
            # Minimal delay (1-2s as discussed)
            await asyncio.sleep(random.uniform(1, 1.5))  # minimal between jobs
            
            # Progress update every 20
            if (idx + 1) % 20 == 0:
                print(f"\n  --- Progress: {idx+1}/{len(remaining)} | Applied: {applied} | Login: {login_needed} | Failed: {failed} | Skipped: {skipped} ---\n", flush=True)
        
        print(f"\n{'=' * 70}")
        print(f"  COMPLETE")
        print(f"  Applied: {applied}")
        print(f"  Login needed: {login_needed}")
        print(f"  Failed: {failed}")
        print(f"  Skipped: {skipped}")
        print(f"  Login URLs saved to: {LOGIN_URLS_FILE}")
        print(f"  Log: {LOG_FILE}")
        print(f"{'=' * 70}")
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
