"""AI-powered browser navigation — uses Claude to understand pages and take actions."""

from __future__ import annotations

import json
import os
from typing import Optional
from playwright.async_api import Page
from humanizer import random_delay
from config import LINKEDIN_EMAIL, LINKEDIN_PASSWORD


# Platform login indicators — if we see these, skip the job
PLATFORM_LOGIN_INDICATORS = [
    "create an account",
    "create account",
    "register to apply",
    "sign up to apply",
    "log in to apply",
    "login to apply",
    "create your account",
    "register now",
    "sign up now",
]

# URL patterns that indicate platform login required
PLATFORM_LOGIN_URL_PATTERNS = [
    "myworkdayjobs.com/*/login",
    "/register",
    "/signup",
    "/create-account",
    "taleo.net",  # Taleo almost always requires account creation
]


def get_client():
    """Get Anthropic client."""
    import anthropic
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))


async def dismiss_overlays(page: Page):
    """Dismiss cookie banners, consent modals, and other overlays."""
    dismiss_selectors = [
        'button:has-text("Accept")',
        'button:has-text("Accept All")',
        'button:has-text("Accept cookies")',
        'button:has-text("Got it")',
        'button:has-text("OK")',
        'button:has-text("Dismiss")',
        'button[aria-label="Dismiss"]',
        'button[aria-label="Close"]',
        '[class*="cookie"] button',
        '[class*="consent"] button',
        '[class*="artdeco-modal__dismiss"]',
    ]
    for selector in dismiss_selectors:
        try:
            btn = await page.query_selector(selector)
            if btn:
                is_visible = await btn.is_visible()
                if is_visible:
                    await btn.click(force=True, timeout=2000)
                    await page.wait_for_timeout(500)
        except Exception:
            continue


async def detect_platform_login(page: Page) -> Optional[str]:
    """Check if the current page requires a platform-specific login/account.

    Returns the reason string if platform login is required, None otherwise.
    Returns None if "Apply with LinkedIn" is available (AI will handle it).
    """
    try:
        # First check: if "Apply with LinkedIn" is available, let the AI handle it
        linkedin_btn = await page.query_selector(
            'button:has-text("Apply with LinkedIn"), a:has-text("Apply with LinkedIn"), '
            'button:has-text("Sign in with LinkedIn"), a:has-text("Sign in with LinkedIn"), '
            'button:has-text("Continue with LinkedIn"), a:has-text("Continue with LinkedIn")'
        )
        if linkedin_btn:
            try:
                is_visible = await linkedin_btn.is_visible()
                if is_visible:
                    # LinkedIn option available — AI will click it, don't flag as platform login
                    return None
            except Exception:
                pass

        current_url = page.url.lower()

        # Don't flag LinkedIn pages as platform login
        if "linkedin.com" in current_url:
            return None

        # Check URL patterns
        for pattern in PLATFORM_LOGIN_URL_PATTERNS:
            if pattern in current_url:
                return f"Platform login required ({pattern})"

        # Check page content for login/register indicators
        body_text = ""
        try:
            body_text = (await page.inner_text("body")).lower()
        except Exception:
            pass

        for indicator in PLATFORM_LOGIN_INDICATORS:
            if indicator in body_text:
                # Make sure it's prominent (not just a footer link)
                btn = await page.query_selector(
                    f'button:has-text("{indicator}"), a:has-text("{indicator}"), '
                    f'h1:has-text("{indicator}"), h2:has-text("{indicator}")'
                )
                if btn:
                    try:
                        is_visible = await btn.is_visible()
                        if is_visible:
                            return f"Platform login required: '{indicator}'"
                    except Exception:
                        pass

        # Check for Workday/Taleo specific login forms
        workday_login = await page.query_selector(
            '[data-automation-id="signIn"], [data-automation-id="createAccount"], '
            'button:has-text("Create Account"), button:has-text("Sign In to Apply")'
        )
        if workday_login:
            try:
                is_visible = await workday_login.is_visible()
                if is_visible:
                    return "Platform login required (Workday/ATS account needed)"
            except Exception:
                pass

    except Exception:
        pass

    return None


async def is_linkedin_signin_page(page: Page) -> bool:
    """Check if the current page is a LinkedIn sign-in page (not a platform login)."""
    url = page.url.lower()
    return any(x in url for x in [
        "linkedin.com/login",
        "linkedin.com/authwall",
        "linkedin.com/uas/login",
        "linkedin.com/checkpoint",
    ])


async def handle_linkedin_signin(page: Page) -> bool:
    """Detect and handle LinkedIn sign-in. Returns True if sign-in was performed."""
    try:
        current_url = page.url.lower()

        # Check if we're on a LinkedIn login/authwall page
        if any(x in current_url for x in ["/login", "/authwall", "/uas/login"]):
            if "linkedin.com" in current_url:
                print("    [ai] Detected LinkedIn sign-in page — logging in...")
                await _do_linkedin_signin(page)
                return True

        # Check if there's a LinkedIn sign-in button on the page (e.g. overlay)
        if "linkedin.com" in current_url:
            signin_btn = await page.query_selector(
                'a:has-text("Sign in"), button:has-text("Sign in"), '
                'a:has-text("Sign in with email"), button:has-text("Sign in with email")'
            )
            if signin_btn:
                is_visible = await signin_btn.is_visible()
                if is_visible:
                    text = (await signin_btn.inner_text()).strip().lower()
                    if "sign in" in text:
                        print(f"    [ai] Found LinkedIn sign-in button: '{text}' — clicking...")
                        await signin_btn.click()
                        await random_delay(2, 3)
                        await _do_linkedin_signin(page)
                        return True
    except Exception as e:
        print(f"    [ai] LinkedIn sign-in check error: {str(e)[:100]}")

    return False


async def _do_linkedin_signin(page: Page):
    """Use AI to fill LinkedIn sign-in form — handles any layout."""
    try:
        client = get_client()

        system_prompt = f"""You are logging into LinkedIn. Fill the email and password fields, then click Sign In.
CREDENTIALS: Email: {LINKEDIN_EMAIL} | Password: {LINKEDIN_PASSWORD}
ACTIONS (respond with ONE JSON per turn):
- {{"type": "FILL", "index": <number>, "value": "text"}}
- {{"type": "CLICK", "index": <number>, "description": "what"}}
- {{"type": "DONE", "reason": "signed in"}}
RULES: Respond with ONLY JSON. Fill email first, then password, then click Sign In."""

        messages = []
        for step in range(6):
            snapshot = await get_page_snapshot(page)
            messages.append({"role": "user", "content": f"Step {step+1}:\n{snapshot}\nAction?"})

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                system=system_prompt,
                messages=messages,
            )
            assistant_msg = response.content[0].text.strip()
            messages.append({"role": "assistant", "content": assistant_msg})

            action = parse_action(assistant_msg)
            if not action:
                continue

            if action.get("type") == "DONE":
                break
            elif action.get("type") == "FILL":
                await fill_element_by_index(page, action["index"], action["value"])
                await random_delay(0.5, 1)
            elif action.get("type") == "CLICK":
                await click_element_by_index(page, action["index"])
                await random_delay(2, 4)

        # Wait for page to settle
        await random_delay(3, 5)
        current_url = page.url
        if "/login" not in current_url and "/authwall" not in current_url:
            print("    [ai] Sign-in successful!")
        else:
            print("    [ai] Still on sign-in page — may need 2FA/captcha")

    except Exception as e:
        print(f"    [ai] Sign-in error: {str(e)[:150]}")


async def get_page_snapshot(page: Page) -> str:
    """Get a text representation of the page that Claude can understand.

    Uses accessibility tree + visible text for a semantic understanding.
    """
    try:
        # Get accessibility tree — gives us all interactive elements
        accessibility = await page.accessibility.snapshot()
        acc_text = format_accessibility_tree(accessibility) if accessibility else ""
    except Exception:
        acc_text = ""

    # Also get visible text content (truncated)
    try:
        body_text = await page.inner_text("body")
        # Truncate to avoid token limits
        body_text = body_text[:3000]
    except Exception:
        body_text = ""

    # Get all clickable/interactive elements with their selectors
    interactive = await get_interactive_elements(page)

    snapshot = f"""PAGE URL: {page.url}
PAGE TITLE: {await page.title()}

INTERACTIVE ELEMENTS:
{interactive}

ACCESSIBILITY TREE:
{acc_text[:4000]}

VISIBLE TEXT (truncated):
{body_text}
"""
    return snapshot


def format_accessibility_tree(node: dict, indent: int = 0) -> str:
    """Format accessibility tree into readable text."""
    if not node:
        return ""

    lines = []
    role = node.get("role", "")
    name = node.get("name", "")
    value = node.get("value", "")

    if name or role in ("button", "link", "textbox", "combobox", "checkbox", "radio"):
        prefix = "  " * indent
        parts = [f"[{role}]"]
        if name:
            parts.append(f'"{name}"')
        if value:
            parts.append(f'value="{value}"')
        lines.append(f"{prefix}{'  '.join(parts)}")

    for child in node.get("children", []):
        lines.append(format_accessibility_tree(child, indent + 1))

    return "\n".join(line for line in lines if line.strip())


async def get_interactive_elements(page: Page) -> str:
    """Get all interactive elements with indices for clicking."""
    elements = await page.evaluate("""() => {
        const results = [];
        const elements = document.querySelectorAll(
            'button, a, input, textarea, select, [role="button"], [role="link"], ' +
            '[role="checkbox"], [role="radio"], [role="combobox"], [role="option"], ' +
            '[tabindex="0"], label'
        );

        let idx = 0;
        for (const el of elements) {
            // Skip hidden elements
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;

            const tag = el.tagName.toLowerCase();
            const type = el.getAttribute('type') || '';
            const text = (el.innerText || el.textContent || '').trim().substring(0, 80);
            const name = el.getAttribute('name') || '';
            const id = el.getAttribute('id') || '';
            const ariaLabel = el.getAttribute('aria-label') || '';
            const placeholder = el.getAttribute('placeholder') || '';
            const value = el.value || '';
            const role = el.getAttribute('role') || '';

            let desc = `[${idx}] <${tag}`;
            if (type) desc += ` type="${type}"`;
            if (id) desc += ` id="${id}"`;
            if (name) desc += ` name="${name}"`;
            if (role) desc += ` role="${role}"`;
            desc += '>';

            if (ariaLabel) desc += ` aria-label="${ariaLabel}"`;
            if (placeholder) desc += ` placeholder="${placeholder}"`;
            if (text && text.length < 60) desc += ` text="${text}"`;
            if (value && tag === 'input') desc += ` value="${value}"`;

            results.push(desc);

            // Store index as data attribute for later retrieval
            el.setAttribute('data-ai-idx', idx.toString());
            idx++;
        }
        return results.join('\\n');
    }""")
    return elements


async def click_element_by_index(page: Page, index: int):
    """Click an element by its AI index."""
    el = await page.query_selector(f'[data-ai-idx="{index}"]')
    if el:
        try:
            await el.scroll_into_view_if_needed()
            await random_delay(0.3, 0.8)
        except Exception:
            pass
        try:
            # First try normal click
            await el.click(timeout=5000)
            return True
        except Exception:
            try:
                # If overlay is blocking, force click
                await el.click(force=True, timeout=5000)
                return True
            except Exception:
                try:
                    # Last resort: use JavaScript click
                    await el.evaluate("el => el.click()")
                    return True
                except Exception:
                    return False
    return False


async def fill_element_by_index(page: Page, index: int, value: str):
    """Fill a text input by its AI index."""
    el = await page.query_selector(f'[data-ai-idx="{index}"]')
    if el:
        try:
            await el.scroll_into_view_if_needed()
        except Exception:
            pass
        await el.click()
        await random_delay(0.2, 0.4)
        # Clear existing value first
        await el.fill("")
        await random_delay(0.1, 0.2)
        await el.fill(value)
        return True
    return False


async def select_element_by_index(page: Page, index: int, value: str):
    """Select a dropdown option by its AI index."""
    el = await page.query_selector(f'[data-ai-idx="{index}"]')
    if el:
        try:
            await el.select_option(label=value)
        except Exception:
            try:
                await el.select_option(value=value)
            except Exception:
                await el.click()
        return True
    return False


async def upload_file_by_index(page: Page, index: int, file_path: str):
    """Upload a file to an input by its AI index.

    Handles both standard <input type="file"> and custom upload buttons
    that trigger a file chooser dialog.
    """
    el = await page.query_selector(f'[data-ai-idx="{index}"]')
    if not el:
        return False

    # Check if it's a standard file input
    tag_name = await el.evaluate("el => el.tagName.toLowerCase()")
    input_type = await el.evaluate("el => el.getAttribute('type') || ''")

    if tag_name == "input" and input_type == "file":
        # Standard file input — use set_input_files directly
        await el.set_input_files(file_path)
        return True
    else:
        # Custom upload button — listen for file chooser event then click
        try:
            async with page.expect_file_chooser(timeout=5000) as fc_info:
                await el.click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(file_path)
            return True
        except Exception:
            # Last resort: look for a hidden file input nearby
            try:
                hidden_input = await page.query_selector('input[type="file"]')
                if hidden_input:
                    await hidden_input.set_input_files(file_path)
                    return True
            except Exception:
                pass
            return False


async def ai_navigate(page: Page, job: dict, application_data: dict, mode: str = "apply") -> dict:
    """Use Claude to navigate the page and apply to a job.

    Flow:
    1. Load job page → handle LinkedIn sign-in if needed
    2. Click Apply (might need to click twice — once on LinkedIn, once on ATS)
    3. If new tab opens → switch to it
    4. If platform login required → skip with reason
    5. Fill form → submit (or scan)

    Returns: {
        "status": "applied"|"scanned"|"skipped"|"failed",
        "notes": "detailed reason",
        "questions": [...],
        "category": "easy_apply"|"external_apply"|"platform_login"|"expired"|"error"
    }
    """
    client = get_client()
    result = {
        "status": "failed",
        "notes": "",
        "questions": [],
        "category": "error",
    }

    resume_path = application_data.get("resume_path", "")
    cover_letter_path = application_data.get("cover_letter_path", "")
    answers = application_data.get("answers", {})

    # Step 1: Dismiss cookie/consent modals
    await dismiss_overlays(page)

    # Step 2: Build the system context for Claude
    # The AI agent handles EVERYTHING: sign-in dialogs, Apply buttons, form filling
    system_prompt = build_system_prompt(job, answers, resume_path, cover_letter_path, mode)

    messages = []
    max_steps = 30

    # Track the active page (may switch if Apply opens a new tab)
    active_page = page
    opened_pages = []

    for step in range(max_steps):
        # Check if active page is still alive
        try:
            await active_page.evaluate("1")
        except Exception:
            result["notes"] = "Page closed or crashed during navigation"
            result["category"] = "error"
            break

        # Get current page state
        snapshot = await get_page_snapshot(active_page)

        # Ask Claude what to do
        messages.append({
            "role": "user",
            "content": f"Step {step + 1}. Here is the current page state:\n\n{snapshot}\n\nWhat action should I take next?"
        })

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=system_prompt,
            messages=messages,
        )

        assistant_msg = response.content[0].text.strip()
        messages.append({"role": "assistant", "content": assistant_msg})

        # Parse the action from Claude's response
        action = parse_action(assistant_msg)

        if not action:
            result["notes"] = f"Step {step+1}: Could not parse action from AI response"
            continue

        # Execute the action
        action_type = action.get("type")

        try:
            if action_type == "DONE":
                result["status"] = action.get("status", "applied")
                result["notes"] = action.get("reason", f"Completed in {step+1} steps")
                result["category"] = "easy_apply" if "linkedin.com" in active_page.url else "external_apply"
                print(f"    [ai] Done: {result['notes']}")
                break

            elif action_type == "CLICK":
                idx = action.get("index")
                desc = action.get("description", "")
                print(f"    [ai] Click [{idx}]: {desc}")

                # Track pages before click to detect new tabs
                context = active_page.context
                pages_before = context.pages[:]

                success = await click_element_by_index(active_page, idx)
                if not success:
                    messages.append({"role": "user", "content": "That element was not found or could not be clicked. Try a different approach."})
                else:
                    # Wait for navigation/modal/new tab to load
                    await random_delay(1.5, 3)

                    # Check if a new tab was opened
                    pages_after = context.pages
                    new_pages = [p for p in pages_after if p not in pages_before]

                    if new_pages:
                        # Switch to the new tab
                        new_page = new_pages[-1]
                        try:
                            await new_page.wait_for_load_state("domcontentloaded", timeout=15000)
                        except Exception:
                            pass
                        print(f"    [ai] New tab opened: {new_page.url[:80]}...")
                        active_page = new_page
                        opened_pages.append(new_page)

                        # Dismiss overlays on new page
                        await dismiss_overlays(active_page)

                        # Check if new page requires platform login (NOT LinkedIn)
                        platform_reason = await detect_platform_login(active_page)
                        if platform_reason:
                            result["status"] = "skipped"
                            result["notes"] = platform_reason
                            result["category"] = "platform_login"
                            result["platform_url"] = active_page.url
                            print(f"    [ai] Platform login required: {active_page.url[:80]}")
                            print(f"    [ai] Reason: {platform_reason}")
                            break

                        # Otherwise, AI will see the new page state on next loop
                    else:
                        # Same page — modal may have opened or page navigated
                        # AI will see the updated state on next loop iteration
                        await random_delay(0.5, 1)

            elif action_type == "FILL":
                idx = action.get("index")
                value = action.get("value", "")
                print(f"    [ai] Fill [{idx}]: '{value[:30]}...' " if len(value) > 30 else f"    [ai] Fill [{idx}]: '{value}'")
                success = await fill_element_by_index(active_page, idx, value)
                if not success:
                    messages.append({"role": "user", "content": "That element was not found. Try a different approach."})
                await random_delay(0.5, 1.5)

            elif action_type == "SELECT":
                idx = action.get("index")
                value = action.get("value", "")
                print(f"    [ai] Select [{idx}]: '{value}'")
                success = await select_element_by_index(active_page, idx, value)
                await random_delay(0.5, 1)

            elif action_type == "UPLOAD":
                idx = action.get("index")
                file_type = action.get("file", "resume")
                path = resume_path if file_type == "resume" else cover_letter_path
                print(f"    [ai] Upload [{idx}]: {file_type}")
                success = await upload_file_by_index(active_page, idx, path)
                await random_delay(1, 2)

            elif action_type == "WAIT":
                await random_delay(2, 4)

            elif action_type == "SKIP":
                result["status"] = "skipped"
                reason = action.get("reason", "AI decided to skip")
                result["notes"] = reason
                # Categorize the skip reason
                reason_lower = reason.lower()
                if any(x in reason_lower for x in ["expired", "no longer", "not found", "closed"]):
                    result["category"] = "expired"
                elif any(x in reason_lower for x in ["login", "account", "register", "sign up"]):
                    result["category"] = "platform_login"
                else:
                    result["category"] = "skipped"
                print(f"    [ai] Skip: {reason}")
                break

            elif action_type == "UNKNOWN_QUESTION":
                question = action.get("question", "")
                result["questions"].append({"label": question, "type": "unknown", "has_answer": False})
                print(f"    [ai] Unknown question: {question}")

            elif action_type == "PLATFORM_LOGIN":
                # AI detected that the page requires platform login
                result["status"] = "skipped"
                result["notes"] = action.get("reason", "Platform login/account required")
                result["category"] = "platform_login"
                result["platform_url"] = active_page.url
                print(f"    [ai] Platform login required: {active_page.url[:80]}")
                print(f"    [ai] Reason: {result['notes']}")
                break

            else:
                result["notes"] = f"Unknown action type: {action_type}"

        except Exception as e:
            error_msg = str(e)[:150]
            print(f"    [ai] Action error: {error_msg}")
            messages.append({"role": "user", "content": f"Action failed with error: {error_msg}. Try a different approach or element."})

    else:
        result["notes"] = f"Reached max steps ({max_steps}) without completing"
        result["category"] = "error"

    # Close any tabs we opened
    for opened_page in opened_pages:
        try:
            if not opened_page.is_closed():
                await opened_page.close()
        except Exception:
            pass

    return result


def build_system_prompt(job: dict, answers: dict, resume_path: str, cover_letter_path: str, mode: str) -> str:
    """Build the system prompt that tells Claude how to navigate."""
    import json as _json

    personal = answers.get("personal", {})
    salary = answers.get("salary", {})
    work_auth = answers.get("work_authorization", {})
    work_history = answers.get("work_history", [])
    education_history = answers.get("education_history", [])
    skills_list = answers.get("skills_list", [])
    certifications = answers.get("certifications", [])

    # Format work history for the prompt
    work_history_text = ""
    for i, job_entry in enumerate(work_history, 1):
        work_history_text += f"""
  JOB {i}:
  - Title: {job_entry.get('job_title', '')}
  - Company: {job_entry.get('company', '')}
  - Location: {job_entry.get('location', '')}
  - Start: {job_entry.get('start_date', '')}
  - End: {job_entry.get('end_date', '')}
  - Current: {'Yes' if job_entry.get('is_current') else 'No'}
  - Description: {job_entry.get('description', '')}
  - Skills: {', '.join(job_entry.get('skills', []))}
"""

    # Format education for the prompt
    education_text = ""
    for i, edu in enumerate(education_history, 1):
        education_text += f"""
  EDUCATION {i}:
  - Degree: {edu.get('degree', '')}
  - Institution: {edu.get('institution', '')}
  - Location: {edu.get('location', '')}
  - Start: {edu.get('start_date', '')}
  - End: {edu.get('end_date', '')}
  - Grade: {edu.get('grade', '')}
  - Field of Study: {edu.get('field_of_study', '')}
  - Description: {edu.get('description', '')}
"""

    answers_text = f"""
APPLICANT INFO:
- Full Name: {personal.get('full_name', 'Nidhi Shetty')}
- First Name: {personal.get('first_name', 'Nidhi')}
- Last Name: {personal.get('last_name', 'Shetty')}
- Email: {personal.get('email', '')}
- Phone: {personal.get('phone', '')}
- Location: {personal.get('location', 'London, UK')}
- City: {personal.get('city', 'London')}
- Country: {personal.get('country', 'United Kingdom')}
- Postcode: {personal.get('postcode', 'E14 7GG')}
- Address: {personal.get('address', '')}
- Date of Birth: {personal.get('date_of_birth', '23/11/1995')}
- Nationality: {personal.get('nationality', 'Indian')}
- LinkedIn: {personal.get('linkedin_url', '')}

WORK AUTHORIZATION:
- Right to work in UK: {work_auth.get('right_to_work_uk', 'Yes')}
- Requires sponsorship: {work_auth.get('require_sponsorship', 'Yes')}
- Visa type: {work_auth.get('visa_type', 'Skilled Worker Visa')}
- Visa expiry: {work_auth.get('visa_expiry', 'April 2027')}

SALARY:
- Expected (analyst level): £{salary.get('expected_salary_analyst', '64000')}
- Expected (senior level): £{salary.get('expected_salary_senior', '90000')}
- Current: £{salary.get('current_salary', '65000')}
- Notice period: 1 month
- Available start: 1 July 2026

WORK HISTORY (add each one-by-one if form asks):
{work_history_text}

EDUCATION (add each one-by-one if form asks):
{education_text}

SKILLS (add each one-by-one if form asks):
{', '.join(skills_list)}

CERTIFICATIONS:
{', '.join(certifications)}

EQUAL OPPORTUNITIES:
- Gender: {personal.get('gender', 'Female')}
- Ethnicity: {personal.get('ethnicity', 'Indian')}
- Disability: {personal.get('disability', 'No')}

FILES AVAILABLE:
- Resume PDF: {resume_path}
- Cover Letter PDF: {cover_letter_path}

LINKEDIN CREDENTIALS (for LinkedIn sign-in ONLY):
- Email: {LINKEDIN_EMAIL}
- Password: {LINKEDIN_PASSWORD}
"""

    mode_instruction = ""
    if mode == "scan":
        mode_instruction = """
MODE: TEST (DRY RUN) — Fill in ALL fields to verify data works, but do NOT click the final Submit button.
- Navigate to the form (click Apply, handle sign-in, click through to the form)
- Fill every field you can with the applicant's data
- Upload resume and cover letter where possible
- Click "Next" to go through all form pages/steps
- For any question you don't have an answer to, use UNKNOWN_QUESTION
- When you reach the FINAL Submit/Apply button (the last step), DO NOT click it
- Instead, respond with DONE status="scanned" and describe what you found
"""
    else:
        mode_instruction = """
MODE: APPLY — Fill in all fields and SUBMIT the application.
"""

    return f"""You are a browser automation agent helping apply to jobs on LinkedIn and other job sites.

JOB: {job.get('title', '')} at {job.get('company', '')}
URL: {job.get('url', '')}
{mode_instruction}

{answers_text}

INSTRUCTIONS:
1. You will see the page state (interactive elements with [index] numbers)
2. Respond with ONE action per turn in this exact JSON format

AVAILABLE ACTIONS:
- {{"type": "CLICK", "index": <number>, "description": "what you're clicking"}}
- {{"type": "FILL", "index": <number>, "value": "text to fill"}}
- {{"type": "SELECT", "index": <number>, "value": "option text to select"}}
- {{"type": "UPLOAD", "index": <number>, "file": "resume" or "cover_letter"}}
- {{"type": "WAIT"}} — wait for page to load
- {{"type": "DONE", "status": "applied" or "scanned", "reason": "explanation"}}
- {{"type": "SKIP", "reason": "why skipping this job"}}
- {{"type": "PLATFORM_LOGIN", "reason": "what platform login is required"}}
- {{"type": "UNKNOWN_QUESTION", "question": "the question text you can't answer"}}

RULES:
- ALWAYS respond with valid JSON only — no extra text

SIGN-IN HANDLING:
- If you see a LinkedIn sign-in dialog (e.g. "Join or sign in", "Sign in with Email" on linkedin.com):
  → Click "Sign in with Email" or "Sign in"
  → FILL the email field with the LinkedIn email
  → FILL the password field with the LinkedIn password
  → CLICK the Sign In button
  → This is LinkedIn's own auth — ALWAYS handle it

- If you see a NON-LinkedIn platform (Workday, Greenhouse, company portal, etc.) that requires login:
  → FIRST: Look for "Apply with LinkedIn" or "Sign in with LinkedIn" button — if found, CLICK it (this uses LinkedIn OAuth, no separate account needed)
  → If "Apply with LinkedIn" opens a LinkedIn auth page, sign in with the LinkedIn credentials above
  → If the platform asks for a verification code, OTP, or something you can't provide → use PLATFORM_LOGIN
  → If there's NO "Apply with LinkedIn" option and it requires creating an account or signing in with a platform-specific login → use PLATFORM_LOGIN action
  → Basically: if you get STUCK at any login/auth barrier you cannot pass, use PLATFORM_LOGIN immediately — don't keep retrying

NAVIGATION FLOW:
1. On the LinkedIn job page, find and click "Apply" or "Easy Apply"
2. If a LinkedIn sign-in dialog appears → sign in using credentials above
3. If a new page/tab loads, look for:
   a. "Apply with LinkedIn" button → click it (preferred on third-party sites)
   b. Another "Apply" button → click it to reach the form
   c. A login/register page with NO LinkedIn option → PLATFORM_LOGIN
4. Keep clicking Apply/Continue until you reach the actual application form
5. Once the form is visible, fill in fields and proceed
6. For Easy Apply modals: fill fields → upload resume + cover letter → click Next → repeat → Submit
7. For external forms: fill fields → upload files → Submit
8. If at any point you encounter a platform login with NO LinkedIn option → PLATFORM_LOGIN

FORM FILLING:
- If a field is already filled correctly, don't change it
- If you see a question you can't answer from the data above, use UNKNOWN_QUESTION
- For yes/no questions about sponsorship: answer Yes
- For "how did you hear about us": LinkedIn
- ALWAYS upload both resume AND cover letter when file inputs are available
- If the job is expired, removed, or not found: use SKIP with reason "Job expired or no longer available"
- After clicking Submit successfully, use DONE with status="applied"

DYNAMIC MULTI-ENTRY FORMS (Education, Work Experience, Skills):
- Many forms let you "Add Education", "Add Experience", "Add Skill" one by one
- Add ALL entries from the data above, one at a time:
  - Fill the subform fields (title, company, dates, description)
  - Click Save/Add/Done for that entry
  - Then click "Add Another" if there are more entries
- For WORK EXPERIENCE: Add all 4 jobs listed above, starting with the most recent
- For EDUCATION: Add all 3 degrees listed above, starting with the most recent
- For SKILLS: Add the most relevant skills (Trade Settlement, Prime Brokerage, Python, Excel, Bloomberg, etc.)
- If you can only add a limited number, prioritize the most recent/relevant ones

IMPORTANT:
- Respond with ONLY the JSON action, nothing else
- ONE action per turn — you will see the updated page after each action
"""


def parse_action(response: str) -> Optional[dict]:
    """Parse an action JSON from Claude's response."""
    # Try to extract JSON from the response
    response = response.strip()

    # If response starts with { it's probably JSON
    if response.startswith("{"):
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

    # Try to find JSON in the response
    import re
    json_match = re.search(r'\{[^{}]+\}', response)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    return None
