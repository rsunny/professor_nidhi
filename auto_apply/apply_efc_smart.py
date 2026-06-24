"""eFinancialCareers Smart Apply — Opens title-filtered jobs, reads desc, applies if relevant.

Reads efc_jobs_title_filtered.json (187 jobs with relevant titles),
visits each page, reads description, uses AI to decide relevance,
and applies immediately if relevant.

Usage:
    python3 -u apply_efc_smart.py
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

from fpdf import FPDF

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

EFC_TITLE_FILTERED = DATA_DIR / "efc_jobs_title_filtered.json"
EFC_STORAGE_FILE = DATA_DIR / "efinancial_storage_state.json"
EFC_SMART_LOG = OUTPUT_DIR / "efc_smart_apply_log.csv"
EFC_SMART_PROGRESS = DATA_DIR / "efc_smart_apply_progress.json"

EMAIL = os.getenv("EFINANCE_EMAIL", "")
PASSWORD = os.getenv("EFINANCE_PASSWORD", "")

# Cover letter output directory
COVER_LETTERS_DIR = OUTPUT_DIR / "cover_letters_generated"


# ---------------------------------------------------------------------------
# Cover Letter Generation
# ---------------------------------------------------------------------------

def generate_cover_letter(client, job: dict, desc: str) -> str:
    """Generate a curated cover letter PDF for this specific job."""
    COVER_LETTERS_DIR.mkdir(parents=True, exist_ok=True)

    title = job.get("title", "Unknown")
    company = job.get("company", "the company")

    # Generate cover letter text
    prompt = f"""Write a concise cover letter (max 250 words) for Nidhi Shetty applying to:

JOB: {title} at {company}
DESCRIPTION: {desc[:1000]}

NIDHI'S BACKGROUND:
- 2.5 years at Morgan Stanley (Prime Brokerage, Glasgow): trade settlement, reconciliation, post-trade operations, corporate actions
- MSc Investment & Risk Finance (Distinction), University of Westminster 2022
- Skills: Excel/VBA (advanced), Bloomberg, CTM, Refinitiv Eikon, trade lifecycle
- Currently: Advertising Account Manager (career change back to finance)
- Visa: Skilled Worker visa, needs sponsorship

RULES:
- Professional, enthusiastic tone
- Highlight relevant experience from Morgan Stanley
- Connect her skills to this specific role
- Keep it to 3-4 short paragraphs
- Do NOT include addresses or "Dear Hiring Manager" header — start directly with content
- End with a brief closing line (not "Yours sincerely")"""

    try:
        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1"),
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        letter_text = response.content[0].text.strip()
    except Exception as e:
        letter_text = f"""I am writing to express my strong interest in the {title} position at {company}.

With 2.5 years of experience at Morgan Stanley in Prime Brokerage operations, I bring hands-on expertise in trade settlement, reconciliation, and post-trade processing. My MSc in Investment & Risk Finance (Distinction) from the University of Westminster provides a strong theoretical foundation to complement my practical experience.

At Morgan Stanley, I managed daily trade settlements across equities and fixed income, performed reconciliations, processed corporate actions, and collaborated with front office teams to resolve trade breaks. I am proficient in Bloomberg, CTM, Refinitiv Eikon, and advanced Excel/VBA.

I am eager to return to financial services operations and believe my combination of practical experience and academic background makes me well-suited for this role. I would welcome the opportunity to discuss how my skills align with your needs."""

    # Generate PDF
    safe_title = re.sub(r'[^\w\s-]', '', title)[:40].replace(' ', '_')
    safe_company = re.sub(r'[^\w\s-]', '', company)[:20].replace(' ', '_')
    filename = f"cl_{safe_company}_{safe_title}.pdf"
    filepath = COVER_LETTERS_DIR / filename

    # Sanitize text for PDF (replace unicode chars)
    def sanitize(text):
        replacements = {
            '\u2014': '-', '\u2013': '-', '\u2018': "'", '\u2019': "'",
            '\u201c': '"', '\u201d': '"', '\u2026': '...', '\u2022': '-',
            '\u00a0': ' ', '\u2010': '-', '\u2011': '-', '\u2012': '-',
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        # Remove any remaining non-latin1 characters
        text = text.encode('latin-1', errors='replace').decode('latin-1')
        return text

    letter_text = sanitize(letter_text)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.set_auto_page_break(auto=True, margin=25)

    # Add name header
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Nidhi Shetty", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    pdf.cell(0, 5, "London, UK | nidhishettyuk24@gmail.com | +44 7440 740078", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)

    # Add body
    pdf.set_font("Helvetica", size=11)
    for paragraph in letter_text.split("\n\n"):
        paragraph = paragraph.strip()
        if paragraph:
            pdf.multi_cell(0, 6, paragraph)
            pdf.ln(4)

    pdf.output(str(filepath))
    return str(filepath)


# Quick rejection patterns in description (conservative — only clear disqualifiers)
DESC_REJECT_PATTERNS = [
    r"(?:fluent|native)\s+(?:in\s+)?(?:french|italian|spanish|korean|japanese|mandarin|arabic|dutch|portuguese|cantonese)",
    r"(?:must\s+(?:be|have)|essential|required).*\b(?:ACA|ACCA|CIMA)\b.*qualif",
    r"\b(?:10|12|15)\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp)",
    r"\bday[\s-]?rate\b",
    r"\bfreelance\s+(?:only|contract)\b",
    r"\brelocation\s+(?:to\s+)?(?:Dubai|Singapore|Hong Kong|Beijing|Tokyo)\s+required\b",
]


# ---------------------------------------------------------------------------
# AI Relevance Check (lightweight)
# ---------------------------------------------------------------------------

def quick_ai_check(client, title: str, desc: str) -> tuple[bool, str]:
    """Quick AI check — ONLY reject if there's a clear disqualifier."""
    prompt = f"""Should Nidhi Shetty apply to this job? She has finance ops experience (Morgan Stanley, trade settlement, reconciliation, middle office).

ONLY say NO if one of these HARD blockers applies:
- Requires a language she doesn't speak (French, Italian, Korean, Japanese, Mandarin, Dutch, Danish, Spanish, Arabic, Cantonese)
- Requires relocation OUTSIDE London/UK
- Is purely a software development/engineering role (NOT trade support engineer or ops engineer — those are OK)
- Requires 10+ years experience
- Is clearly a senior leadership role (Managing Director, C-suite)
- Is a part-time or internship role

For ANYTHING else — even if it seems slightly senior, slightly different field, contract, FTC — say YES. When in doubt, say YES.

JOB: {title}
DESCRIPTION (first 1000 chars):
{desc[:1000]}

Reply ONLY: YES or NO"""

    try:
        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1"),
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response.content[0].text.strip().upper()
        return answer.startswith("YES"), answer
    except Exception as e:
        # On error, be conservative — skip
        return False, f"Error: {e}"


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

async def apply_on_page(page: Page, job: dict, client) -> tuple[str, str]:
    """Click apply and fill the form. Handles external redirects."""
    # Remember current URL to detect redirects
    original_url = page.url

    # Find Apply button
    apply_btn = page.locator(
        'button:has-text("Apply"), '
        'a:has-text("Apply"), '
        'button:has-text("Quick apply"), '
        'a:has-text("Quick apply"), '
        '[data-gtm-trackable*="Apply"]'
    ).first

    try:
        if not await apply_btn.is_visible(timeout=5000):
            return "no_apply_button", "Apply button not found"
    except Exception:
        return "no_apply_button", "Apply button not found"

    # Check if Apply button is a link to external site
    apply_href = await apply_btn.get_attribute("href") or ""
    if apply_href and "efinancialcareers" not in apply_href and apply_href.startswith("http"):
        # External apply link — navigate there
        print(f"      External apply link: {apply_href[:60]}")
        await page.goto(apply_href, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
    else:
        await apply_btn.click()
        await asyncio.sleep(random.uniform(2, 4))

    # Check if we got redirected to an external site
    current_url = page.url
    if "efinancialcareers" not in current_url.lower():
        domain = current_url.split("/")[2] if "/" in current_url else "unknown"
        print(f"      Redirected to external: {domain}")

        # Check if login is REQUIRED (not just a nav link)
        # A login page has: input[type=password] visible + no application form
        login_required = await page.evaluate("""() => {
            // Check for visible password fields (actual login forms)
            const pwFields = document.querySelectorAll('input[type="password"]');
            for (const f of pwFields) {
                const rect = f.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) return true;
            }
            // Check if it's a Workday login page
            if (document.querySelector('[data-automation-id="signIn"], [data-automation-id="login"]')) return true;
            // Check if main content is just a login form
            const body = document.body.innerText.toLowerCase();
            if (body.length < 500 && (body.includes('sign in') || body.includes('log in'))) return true;
            return false;
        }""")

        if login_required:
            log_external_domain(domain, job, needs_login=True)
            return "external_login_needed", f"External site needs login: {domain}"

        # No login needed — try to fill the external application form
        print(f"      Filling external application on {domain}...")
        return await ai_fill_form(page, job, client)

    # Still on eFC — check if already applied or quick-apply succeeded
    try:
        body = await page.inner_text("body")
        if "already applied" in body.lower():
            return "already_applied", "Already applied"
        if "application submitted" in body.lower() or "thank you" in body.lower():
            return "applied", "Quick-applied successfully (one-click)"
    except Exception:
        pass

    # Check if eFC opened a new tab (some Apply buttons open in new tab)
    pages = page.context.pages
    if len(pages) > 1:
        new_page = pages[-1]
        await asyncio.sleep(3)  # Wait for page to load
        new_url = new_page.url
        if "efinancialcareers" not in new_url.lower() and new_url != "about:blank":
            domain = new_url.split("/")[2] if "/" in new_url else "unknown"
            print(f"      Opened external tab: {domain}")

            # Check for actual login requirement (password field visible)
            login_required = await new_page.evaluate("""() => {
                const pwFields = document.querySelectorAll('input[type="password"]');
                for (const f of pwFields) {
                    const rect = f.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) return true;
                }
                if (document.querySelector('[data-automation-id="signIn"], [data-automation-id="login"]')) return true;
                const body = document.body.innerText.toLowerCase();
                if (body.length < 500 && (body.includes('sign in') || body.includes('log in'))) return true;
                return false;
            }""")

            if login_required:
                log_external_domain(domain, job, needs_login=True)
                await new_page.close()
                return "external_login_needed", f"External site needs login: {domain}"

            # Try to fill form on the new tab
            print(f"      Filling external form on {domain}...")
            result = await ai_fill_form(new_page, job, client)
            await new_page.close()
            return result

    # Still on eFC form — fill it
    return await ai_fill_form(page, job, client)


# Track external domains
EXTERNAL_DOMAINS_FILE = OUTPUT_DIR / "efc_external_domains.txt"

def log_external_domain(domain: str, job: dict, needs_login: bool = False):
    """Log external domain that needs attention."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(EXTERNAL_DOMAINS_FILE, "a") as f:
        status = "NEEDS LOGIN" if needs_login else "NO LOGIN"
        f.write(f"{domain} | {status} | {job.get('title', '')[:60]} | {job.get('url', '')}\n")


async def ai_fill_form(page: Page, job: dict, client) -> tuple[str, str]:
    """AI-driven form filling."""
    resume_path = str(RESUME_PATH)
    set_current_job(job)
    # Use curated cover letter if generated, otherwise fall back
    cl_path = job.get("_cover_letter_path") or get_cover_letter_for_job(job) or ""

    system_prompt = f"""You are filling a job application form on eFinancialCareers.

JOB: {job.get('title', 'Unknown')} at {job.get('company', 'Unknown')}

You have these tools:
- lookup_answer: Call for EVERY question/field. Returns the correct answer.
- fill_field: Fill a text input
- select_option: Select from dropdown
- click_element: Click buttons, radios, checkboxes
- upload_file: Upload resume/cover letter
- done: Signal completion

WORKFLOW:
1. Look at form elements
2. For each empty field: lookup_answer, then fill_field/select_option
3. Upload CV when you see file upload
4. Upload cover letter if available
5. Click Submit/Apply
6. Call done(status="applied")

RULES:
- lookup_answer BEFORE filling any field
- Skip already-filled fields
- If you see "already applied", done(status="already_applied")
- If success/thank you message, done(status="applied")
- Cover letter: {cl_path or 'generic'}
"""

    messages = []
    for step in range(20):
        try:
            page_content = await get_page_elements(page)
        except Exception:
            await asyncio.sleep(2)
            continue

        if not page_content.strip():
            await asyncio.sleep(2)
            continue

        messages.append({"role": "user", "content": f"Step {step+1}. Elements:\n\n{page_content}"})

        try:
            response = client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1"),
                max_tokens=1024,
                system=system_prompt,
                messages=messages,
                tools=FORM_TOOLS,
            )
        except Exception as e:
            return "api_error", f"API failed: {str(e)[:100]}"

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
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result_str})
                elif block.name == "done":
                    done_status = block.input.get("status", "applied")
                    reason = block.input.get("reason", "")
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
                if "thank" in body.lower() or "submitted" in body.lower():
                    return "applied", "Success detected"
            except Exception:
                pass

    return "max_steps", "Max steps reached"


async def get_page_elements(page: Page) -> str:
    """Get interactive elements."""
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
    file_exists = EFC_SMART_LOG.exists()
    with open(EFC_SMART_LOG, "a", newline="") as f:
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
    if EFC_SMART_PROGRESS.exists():
        return set(json.loads(EFC_SMART_PROGRESS.read_text()))
    return set()


def save_progress(processed: set):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EFC_SMART_PROGRESS.write_text(json.dumps(list(processed)))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("  eFinancialCareers SMART APPLY")
    print("  Open → Read → Decide → Apply (if relevant)")
    print("=" * 60, flush=True)

    if not EMAIL or not PASSWORD:
        print("  ERROR: EFINANCE_EMAIL/EFINANCE_PASSWORD not set")
        return

    jobs = json.loads(EFC_TITLE_FILTERED.read_text())
    print(f"  Title-filtered jobs: {len(jobs)}")

    # Load progress
    processed = load_progress()
    remaining = [j for j in jobs if j["url"] not in processed]
    print(f"  Already processed: {len(processed)}")
    print(f"  Remaining: {len(remaining)}")

    if not remaining:
        print("  Nothing to process.")
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

        # Check login
        await page.goto("https://www.efinancialcareers.co.uk/", wait_until="domcontentloaded")
        await asyncio.sleep(3)

        client = get_client()
        applied_count = 0
        skipped_count = 0
        irrelevant_count = 0

        for idx, job in enumerate(remaining):
            title = job.get("title", "Unknown")
            print(f"\n  [{idx+1}/{len(remaining)}] {title[:65]}")

            try:
                # Step 1: Open the job page
                await page.goto(job["url"], wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(2, 3))

                # Check for login redirect
                if "login" in page.url.lower() or "signin" in page.url.lower():
                    print("    Session expired — re-logging in...")
                    await page.goto("https://www.efinancialcareers.co.uk/login", wait_until="domcontentloaded")
                    await asyncio.sleep(2)
                    await page.fill('#email', EMAIL)
                    await asyncio.sleep(0.5)
                    await page.fill('#password', PASSWORD)
                    await asyncio.sleep(0.5)
                    await page.click('button.submit, button[type="submit"]')
                    await asyncio.sleep(5)
                    # Re-navigate to job
                    await page.goto(job["url"], wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(2)

                # Step 2: Read description
                page_text = await page.inner_text("body")

                if "page not found" in page_text.lower() or len(page_text) < 100:
                    print("    404/empty — skipping")
                    log_result(job, "expired", "Page not found")
                    skipped_count += 1
                    processed.add(job["url"])
                    save_progress(processed)
                    continue

                if "already applied" in page_text.lower():
                    print("    Already applied — skipping")
                    log_result(job, "already_applied", "Already applied")
                    skipped_count += 1
                    processed.add(job["url"])
                    save_progress(processed)
                    continue

                # Step 3: Quick description rejection (regex patterns)
                desc_text = page_text[:3000].lower()
                rejected_by_pattern = False
                for pat in DESC_REJECT_PATTERNS:
                    if re.search(pat, desc_text, re.IGNORECASE):
                        print(f"    REJECT (pattern): {pat[:40]}")
                        log_result(job, "irrelevant", f"Pattern reject: {pat[:60]}")
                        irrelevant_count += 1
                        rejected_by_pattern = True
                        break

                if rejected_by_pattern:
                    processed.add(job["url"])
                    save_progress(processed)
                    continue

                # Step 4: AI relevance check
                is_relevant, ai_answer = quick_ai_check(client, title, page_text[:2000])

                if not is_relevant:
                    print(f"    REJECT (AI): {ai_answer[:50]}")
                    log_result(job, "irrelevant", f"AI: {ai_answer}")
                    irrelevant_count += 1
                    processed.add(job["url"])
                    save_progress(processed)
                    continue

                # Step 5: Generate curated cover letter
                print(f"    RELEVANT — Generating cover letter...")
                cl_path = generate_cover_letter(client, job, page_text[:2000])
                job["_cover_letter_path"] = cl_path

                # Step 6: Apply!
                print(f"    Applying...")
                status, reason = await apply_on_page(page, job, client)

                if status == "applied":
                    applied_count += 1
                    print(f"    APPLIED!")
                elif status == "already_applied":
                    skipped_count += 1
                    print(f"    Already applied")
                else:
                    skipped_count += 1
                    print(f"    {status}: {reason[:60]}")

                log_result(job, status, reason)

            except Exception as e:
                print(f"    ERROR: {str(e)[:80]}")
                log_result(job, "error", str(e)[:200])
                skipped_count += 1

            processed.add(job["url"])
            save_progress(processed)

            # Rate limiting
            await asyncio.sleep(random.uniform(2, 4))

            if (idx + 1) % 10 == 0:
                print(f"\n  --- Progress: {idx+1}/{len(remaining)} | Applied: {applied_count} | Irrelevant: {irrelevant_count} | Skipped: {skipped_count} ---", flush=True)

        print(f"\n{'=' * 60}")
        print(f"  COMPLETE")
        print(f"  Applied: {applied_count}")
        print(f"  Irrelevant (rejected): {irrelevant_count}")
        print(f"  Skipped (other): {skipped_count}")
        print(f"  Total processed: {len(processed)}")
        print(f"{'=' * 60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
