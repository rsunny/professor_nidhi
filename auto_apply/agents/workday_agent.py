"""Workday Form Agent — fills ONE page of a Workday application form.

Called in a LOOP by the orchestrator — one invocation per page.
Fresh context each page (prevents context overflow on 15-page forms).

Key Workday specifics:
- Uses [data-automation-id] attributes extensively
- Multi-page wizard with progress indicators
- Custom dropdowns (click to open, then select from list)
- File uploads via specific automation IDs
- "My Experience" section with add/remove entries

Model: haiku (cheap enough for 50 steps across all pages)
Max steps per page: 8
"""

from __future__ import annotations

import os
from playwright.async_api import Page

from . import (
    AgentResult, FORM_TOOLS, run_agent, set_current_job,
    check_success_indicators, random_delay,
)


WORKDAY_SYSTEM_PROMPT = """You are filling ONE PAGE of a Workday job application form.

WORKDAY-SPECIFIC RULES:
1. Look for [data-automation-id] attributes — they identify Workday elements
2. Workday dropdowns: click the field first to open the dropdown, then click/select the option
3. File uploads use data-automation-id like "file-upload-input-ref"
4. Progress is shown via step indicators at the top
5. Fill ALL visible empty fields on this page using lookup_answer
6. After filling everything, click the Next/Continue/Submit button
7. Signal done(status="page_complete") after clicking Next (new page will load)
8. Signal done(status="submitted") if you see a success/thank you message
9. Signal done(status="stuck") if you cannot proceed (error messages, missing required fields)

COMMON WORKDAY FIELD IDS:
- data-automation-id="legalNameSection_firstName" → First name
- data-automation-id="legalNameSection_lastName" → Last name
- data-automation-id="email" → Email
- data-automation-id="phone-number" → Phone
- data-automation-id="addressSection_countryRegion" → Country dropdown
- data-automation-id="addressSection_city" → City
- data-automation-id="file-upload-input-ref" → Resume upload
- data-automation-id="bottom-navigation-next-button" → Next button
- data-automation-id="bottom-navigation-submit-button" → Submit button

DROPDOWN HANDLING:
- Workday dropdowns are NOT standard <select> elements
- They appear as clickable divs/buttons with data-automation-id
- Click the dropdown to open it → wait → then click the option text
- Options usually appear in a popup/listbox

MULTI-ENTRY SECTIONS:
- "My Experience" / "Work Experience" — add entries one by one
- "Education" — add each degree
- Click "Add" button, fill the sub-form, then it saves automatically
- Fill ALL entries from the applicant's profile

WORKFLOW:
1. Look at visible form fields
2. For each empty field: call lookup_answer with the field label/question
3. Use the answer to fill_field or select_option or click_element
4. Upload resume/cover letter when you see file upload inputs
5. Click Next/Continue/Submit when all fields are filled
6. Signal page_complete or submitted

IMPORTANT:
- Fill fields in order (top to bottom)
- If a field has a validation error, try to fix it
- If you see "already applied" or success text → done(status="submitted")
- Do NOT skip required fields
"""


async def fill_workday_page(page: Page, job: dict, resume_path: str, cover_letter_path: str = "") -> AgentResult:
    """Fill one page of a Workday form.

    Called repeatedly by the orchestrator until submitted or stuck.
    """
    set_current_job(job)

    # Check for success before starting
    if await check_success_indicators(page):
        return AgentResult(success=True, status="submitted", data={"reason": "Success page detected"})

    # Check for "already applied"
    try:
        body = (await page.inner_text("body")).lower()
        if "already applied" in body or "previously applied" in body:
            return AgentResult(success=True, status="submitted", data={"reason": "Already applied"})
    except Exception:
        pass

    result = await run_agent(
        page=page,
        system_prompt=WORKDAY_SYSTEM_PROMPT,
        tools=FORM_TOOLS,
        max_steps=8,
        model_tier="haiku",
        resume_path=resume_path,
        cover_letter_path=cover_letter_path,
        context_window=6,
    )

    return result


async def workday_orchestrator_loop(page: Page, job: dict, resume_path: str,
                                     cover_letter_path: str = "", max_pages: int = 20) -> AgentResult:
    """Loop through Workday pages, calling fill_workday_page per page.

    This is the main entry point for Workday applications.
    """
    print(f"    [workday] Starting multi-page form fill...")

    for page_num in range(max_pages):
        print(f"    [workday] Page {page_num + 1}...")

        result = await fill_workday_page(page, job, resume_path, cover_letter_path)

        if result.status == "submitted" or result.status == "applied":
            print(f"    [workday] Submitted on page {page_num + 1}!")
            return AgentResult(success=True, status="applied",
                               data={"pages_filled": page_num + 1, "reason": result.data.get("reason", "")})

        elif result.status in ("error", "stuck", "max_steps"):
            print(f"    [workday] Stuck on page {page_num + 1}: {result.error}")
            # Try clicking Next one more time
            try:
                next_btn = page.locator(
                    'button[data-automation-id="bottom-navigation-next-button"], '
                    'button:has-text("Next"), button:has-text("Continue")'
                ).first
                if await next_btn.is_visible(timeout=2000):
                    await next_btn.click()
                    await random_delay(2, 3)
                    continue  # Try next page
            except Exception:
                pass
            return AgentResult(success=False, status="stuck",
                               error=f"Stuck on page {page_num + 1}: {result.error}",
                               data={"pages_filled": page_num})

        elif result.status == "page_complete":
            # Wait for next page to load
            await random_delay(2, 4)
            continue

        elif result.status == "expired":
            return AgentResult(success=False, status="expired",
                               data={"reason": "Job expired during form fill"})

        elif result.status == "skipped":
            return AgentResult(success=False, status="skipped",
                               data={"reason": result.data.get("reason", "Skipped")})

    # Exhausted all pages
    return AgentResult(success=False, status="max_pages",
                       error=f"Filled {max_pages} pages without submission")
