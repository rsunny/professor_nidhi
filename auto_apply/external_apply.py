"""External ATS form filler — handles Workday, Greenhouse, Lever, etc."""

from __future__ import annotations

import asyncio
from typing import List
from playwright.async_api import Page, BrowserContext
from config import RESUME_PATH, get_application_answers
from form_filler import detect_and_fill_fields, scan_form_questions
from cover_letter_manager import get_cover_letter_pdf_path
from humanizer import random_delay
from logger import log_application


# ATS detection patterns
ATS_PATTERNS = {
    "workday": ["myworkdayjobs.com", "workday.com", "wd5.myworkdayjobs", "wd3.myworkdayjobs"],
    "greenhouse": ["greenhouse.io", "boards.greenhouse.io"],
    "lever": ["lever.co", "jobs.lever.co"],
    "smartrecruiters": ["smartrecruiters.com", "jobs.smartrecruiters.com"],
    "icims": ["icims.com"],
    "taleo": ["taleo.net"],
    "reed": ["reed.co.uk"],
}


def detect_ats(url: str) -> str:
    """Detect which ATS system a URL belongs to."""
    url_lower = url.lower()
    for ats_name, patterns in ATS_PATTERNS.items():
        for pattern in patterns:
            if pattern in url_lower:
                return ats_name
    return "unknown"


async def handle_external_apply(page: Page, context: BrowserContext, job: dict, mode: str = "apply") -> dict:
    """Handle external application flow.

    Detects the ATS and routes to the appropriate handler.
    """
    result = {"status": "failed", "notes": "", "questions": []}

    try:
        # Navigate to job URL on LinkedIn first
        await page.goto(job["url"], wait_until="domcontentloaded")
        await random_delay(2, 4)

        # Find the Apply button (external)
        apply_btn = await find_external_apply_button(page)
        if not apply_btn:
            result["status"] = "skipped"
            result["notes"] = "No external Apply button found"
            return result

        # Click Apply — might open new tab
        async with context.expect_page() as new_page_info:
            await apply_btn.click()

        try:
            new_page = await asyncio.wait_for(new_page_info.value, timeout=10)
            await new_page.wait_for_load_state("domcontentloaded")
            target_page = new_page
        except (asyncio.TimeoutError, Exception):
            # No new tab — might have redirected in same tab
            target_page = page
            await random_delay(2, 3)

        external_url = target_page.url
        ats = detect_ats(external_url)
        print(f"[external] Detected ATS: {ats} for {job['company']} ({external_url[:80]}...)")

        if mode == "scan":
            questions = await scan_external_form(target_page, ats)
            result["status"] = "scanned"
            result["questions"] = questions
            result["notes"] = f"Scanned {ats} form, found {len(questions)} questions"
        else:
            # Route to appropriate handler
            if ats == "workday":
                result = await handle_workday(target_page, job)
            elif ats == "greenhouse":
                result = await handle_greenhouse(target_page, job)
            elif ats == "lever":
                result = await handle_lever(target_page, job)
            elif ats == "reed":
                result = await handle_reed(target_page, job)
            else:
                result = await handle_generic_ats(target_page, job)

        # Close new tab if we opened one
        if target_page != page:
            await target_page.close()

    except Exception as e:
        result["notes"] = f"External apply error: {str(e)[:200]}"

    return result


async def find_external_apply_button(page: Page):
    """Find the Apply button that redirects to an external site."""
    selectors = [
        'a[class*="apply-button"]',
        'button[class*="apply-button"]',
        'a:has-text("Apply")',
        'button:has-text("Apply")',
        '[class*="jobs-apply-button"]',
    ]
    for selector in selectors:
        btn = await page.query_selector(selector)
        if btn:
            text = (await btn.inner_text()).strip().lower()
            # Make sure it's NOT "Easy Apply"
            if "easy apply" not in text and "apply" in text:
                return btn

    # Fallback
    buttons = await page.query_selector_all('a, button')
    for btn in buttons:
        text = (await btn.inner_text()).strip().lower()
        if text == "apply" or text == "apply now" or text == "apply on company website":
            return btn

    return None


async def scan_external_form(page: Page, ats: str) -> List[dict]:
    """Scan an external form for questions without filling."""
    await random_delay(2, 4)
    questions = await scan_form_questions(page)
    return questions


async def handle_workday(page: Page, job: dict) -> dict:
    """Handle Workday application forms."""
    result = {"status": "failed", "notes": "", "questions": []}
    answers = get_application_answers()

    try:
        await random_delay(2, 4)

        # Workday often requires creating an account first
        # Look for "Apply Manually" or "Autofill with Resume"
        autofill_btn = await page.query_selector('button:has-text("Autofill"), button:has-text("Upload")')
        if autofill_btn:
            await autofill_btn.click()
            await random_delay(1, 2)

            # Upload resume
            file_input = await page.query_selector('input[type="file"]')
            if file_input:
                await file_input.set_input_files(RESUME_PATH)
                await random_delay(3, 5)

        # Fill form fields
        unknown = await detect_and_fill_fields(page, job)
        result["questions"] = unknown

        # Upload cover letter if available
        await upload_cover_letter_external(page, job)

        # Look for Submit
        submit_btn = await page.query_selector('button:has-text("Submit"), button[type="submit"]')
        if submit_btn:
            await submit_btn.click()
            await random_delay(3, 5)
            result["status"] = "applied"
            result["notes"] = "Applied via Workday"
        else:
            result["notes"] = "Workday: Could not find Submit button"

    except Exception as e:
        result["notes"] = f"Workday error: {str(e)[:150]}"

    return result


async def handle_greenhouse(page: Page, job: dict) -> dict:
    """Handle Greenhouse application forms."""
    result = {"status": "failed", "notes": "", "questions": []}

    try:
        await random_delay(2, 4)

        # Greenhouse typically has a simple form
        # Fill name fields
        answers = get_application_answers()
        personal = answers.get("personal", {})

        # Common Greenhouse field IDs
        field_map = {
            '#first_name': personal.get("first_name", ""),
            '#last_name': personal.get("last_name", ""),
            '#email': personal.get("email", ""),
            '#phone': personal.get("phone", ""),
        }

        for selector, value in field_map.items():
            el = await page.query_selector(selector)
            if el and value:
                await el.fill(value)
                await random_delay(0.3, 0.7)

        # Upload resume
        resume_input = await page.query_selector('input[type="file"][id*="resume"], input[name*="resume"]')
        if not resume_input:
            resume_input = await page.query_selector('input[type="file"]')
        if resume_input:
            await resume_input.set_input_files(RESUME_PATH)
            await random_delay(2, 3)

        # Upload cover letter
        await upload_cover_letter_external(page, job)

        # Fill additional questions
        unknown = await detect_and_fill_fields(page, job)
        result["questions"] = unknown

        # Submit
        submit_btn = await page.query_selector('input[type="submit"], button[type="submit"], button:has-text("Submit")')
        if submit_btn:
            await submit_btn.click()
            await random_delay(3, 5)
            result["status"] = "applied"
            result["notes"] = "Applied via Greenhouse"
        else:
            result["notes"] = "Greenhouse: Could not find Submit button"

    except Exception as e:
        result["notes"] = f"Greenhouse error: {str(e)[:150]}"

    return result


async def handle_lever(page: Page, job: dict) -> dict:
    """Handle Lever application forms."""
    result = {"status": "failed", "notes": "", "questions": []}

    try:
        await random_delay(2, 4)

        # Click "Apply for this job" if visible
        apply_btn = await page.query_selector('a:has-text("Apply"), button:has-text("Apply")')
        if apply_btn:
            await apply_btn.click()
            await random_delay(2, 3)

        # Fill standard Lever fields
        answers = get_application_answers()
        personal = answers.get("personal", {})

        field_map = {
            'input[name="name"]': personal.get("full_name", ""),
            'input[name="email"]': personal.get("email", ""),
            'input[name="phone"]': personal.get("phone", ""),
            'input[name="org"]': answers.get("employment", {}).get("current_employer", ""),
            'input[name="urls[LinkedIn]"]': personal.get("linkedin_url", ""),
        }

        for selector, value in field_map.items():
            el = await page.query_selector(selector)
            if el and value:
                await el.fill(value)
                await random_delay(0.3, 0.7)

        # Upload resume
        resume_input = await page.query_selector('input[name="resume"]')
        if not resume_input:
            resume_input = await page.query_selector('input[type="file"]')
        if resume_input:
            await resume_input.set_input_files(RESUME_PATH)
            await random_delay(2, 3)

        # Upload cover letter
        cover_input = await page.query_selector('input[name="coverLetter"], input[name="cover_letter"]')
        if cover_input:
            cover_path = get_cover_letter_pdf_path(job)
            await cover_input.set_input_files(cover_path)
            await random_delay(1, 2)

        # Fill additional fields
        unknown = await detect_and_fill_fields(page, job)
        result["questions"] = unknown

        # Submit
        submit_btn = await page.query_selector('button[type="submit"], button:has-text("Submit")')
        if submit_btn:
            await submit_btn.click()
            await random_delay(3, 5)
            result["status"] = "applied"
            result["notes"] = "Applied via Lever"
        else:
            result["notes"] = "Lever: Could not find Submit button"

    except Exception as e:
        result["notes"] = f"Lever error: {str(e)[:150]}"

    return result


async def handle_reed(page: Page, job: dict) -> dict:
    """Handle Reed.co.uk application forms."""
    result = {"status": "failed", "notes": "", "questions": []}

    try:
        await random_delay(2, 4)

        # Reed usually has an "Apply now" button
        apply_btn = await page.query_selector('a:has-text("Apply now"), button:has-text("Apply")')
        if apply_btn:
            await apply_btn.click()
            await random_delay(2, 3)

        # Reed may require login — fill form if present
        unknown = await detect_and_fill_fields(page, job)
        result["questions"] = unknown

        # Upload CV if option exists
        file_input = await page.query_selector('input[type="file"]')
        if file_input:
            await file_input.set_input_files(RESUME_PATH)
            await random_delay(2, 3)

        # Add cover letter text if textarea available
        cover_textarea = await page.query_selector('textarea[name*="cover"], textarea[id*="cover"]')
        if cover_textarea:
            from cover_letter_manager import find_cover_letter_for_job
            letter = find_cover_letter_for_job(job)
            if letter:
                await cover_textarea.fill(letter)

        # Submit
        submit_btn = await page.query_selector('button[type="submit"], input[type="submit"], button:has-text("Apply")')
        if submit_btn:
            await submit_btn.click()
            await random_delay(3, 5)
            result["status"] = "applied"
            result["notes"] = "Applied via Reed"
        else:
            result["notes"] = "Reed: Could not find Submit button"

    except Exception as e:
        result["notes"] = f"Reed error: {str(e)[:150]}"

    return result


async def handle_generic_ats(page: Page, job: dict) -> dict:
    """Generic handler for unknown ATS systems."""
    result = {"status": "failed", "notes": "", "questions": []}

    try:
        await random_delay(2, 4)

        # Upload resume if file input exists
        file_inputs = await page.query_selector_all('input[type="file"]')
        if file_inputs:
            # First file input usually for resume
            await file_inputs[0].set_input_files(RESUME_PATH)
            await random_delay(2, 3)

            # Second file input for cover letter
            if len(file_inputs) > 1:
                cover_path = get_cover_letter_pdf_path(job)
                await file_inputs[1].set_input_files(cover_path)
                await random_delay(1, 2)

        # Fill all detectable fields
        unknown = await detect_and_fill_fields(page, job)
        result["questions"] = unknown

        # Try to find submit
        submit_btn = await page.query_selector(
            'button[type="submit"], input[type="submit"], '
            'button:has-text("Submit"), button:has-text("Apply")'
        )
        if submit_btn:
            await submit_btn.click()
            await random_delay(3, 5)
            result["status"] = "applied"
            result["notes"] = f"Applied via generic handler (URL: {page.url[:60]})"
        else:
            result["status"] = "failed"
            result["notes"] = f"Generic ATS: Could not find Submit button ({page.url[:60]})"

    except Exception as e:
        result["notes"] = f"Generic ATS error: {str(e)[:150]}"

    return result


async def upload_cover_letter_external(page: Page, job: dict):
    """Try to upload a cover letter on an external ATS form."""
    # Look for cover letter file input
    file_inputs = await page.query_selector_all('input[type="file"]')

    for file_input in file_inputs:
        # Check if this is a cover letter field (not resume)
        parent_text = ""
        try:
            parent = await file_input.evaluate_handle("el => el.closest('div')")
            parent_text = (await parent.inner_text()).lower()
        except Exception:
            pass

        label = await file_input.get_attribute("aria-label") or ""
        name = await file_input.get_attribute("name") or ""

        if any(kw in (parent_text + label + name).lower() for kw in ["cover", "letter", "additional"]):
            cover_path = get_cover_letter_pdf_path(job)
            await file_input.set_input_files(cover_path)
            print(f"[external] Uploaded cover letter for {job.get('company', '')}")
            return

    # If only one file input was found and resume already uploaded, look for a second upload option
    # Some forms have "Add another document" buttons
    add_btn = await page.query_selector('button:has-text("Add"), a:has-text("additional document")')
    if add_btn:
        await add_btn.click()
        await random_delay(1, 2)
        new_input = await page.query_selector('input[type="file"]:not([data-used])')
        if new_input:
            cover_path = get_cover_letter_pdf_path(job)
            await new_input.set_input_files(cover_path)
            print(f"[external] Uploaded cover letter (via Add button) for {job.get('company', '')}")
