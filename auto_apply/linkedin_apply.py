"""LinkedIn Easy Apply handler — multi-step modal navigation."""

from __future__ import annotations

import asyncio
from typing import List
from playwright.async_api import Page
from config import RESUME_PATH, get_application_answers
from form_filler import detect_and_fill_fields, scan_form_questions, match_label_to_answer
from cover_letter_manager import get_cover_letter_pdf_path
from humanizer import random_delay
from logger import log_application


async def handle_easy_apply(page: Page, job: dict, mode: str = "apply") -> dict:
    """Handle the LinkedIn Easy Apply flow for a job.

    Args:
        page: Playwright page instance
        job: Job dict with id, title, company, url
        mode: "apply" to submit, "scan" to just collect questions

    Returns:
        dict with status and notes
    """
    result = {"status": "failed", "notes": "", "questions": []}

    try:
        # Navigate to job page
        await page.goto(job["url"], wait_until="domcontentloaded")
        await random_delay(2, 4)

        # Check if job still exists
        if await page.query_selector('[class*="not-found"], [class*="expired"]'):
            result["status"] = "expired"
            result["notes"] = "Job listing no longer available"
            return result

        # Look for Easy Apply button
        easy_apply_btn = await find_easy_apply_button(page)
        if not easy_apply_btn:
            result["status"] = "skipped"
            result["notes"] = "No Easy Apply button found — may require external application"
            return result

        # Click Easy Apply
        await easy_apply_btn.click()
        await random_delay(1.5, 3)

        # Wait for modal to appear
        modal = await page.wait_for_selector(
            '[class*="jobs-easy-apply-modal"], [class*="artdeco-modal"], [role="dialog"]',
            timeout=10000,
        )
        if not modal:
            result["notes"] = "Easy Apply modal did not appear"
            return result

        # Process multi-step form
        all_questions = []
        step = 0
        max_steps = 10  # Safety limit

        while step < max_steps:
            step += 1
            await random_delay(1, 2)

            if mode == "scan":
                # Just collect questions
                questions = await scan_form_questions(page)
                all_questions.extend(questions)
            else:
                # Fill form fields
                unknown = await fill_easy_apply_step(page, job)
                all_questions.extend(unknown)

            # Check for Submit button
            submit_btn = await find_button(page, ["submit application", "submit", "send application"])
            if submit_btn:
                if mode == "apply":
                    await submit_btn.click()
                    await random_delay(2, 4)

                    # Check for success
                    success = await check_submission_success(page)
                    if success:
                        result["status"] = "applied"
                        result["notes"] = f"Successfully applied via Easy Apply (steps: {step})"
                    else:
                        result["notes"] = f"Submit clicked but success not confirmed (step {step})"
                else:
                    result["status"] = "scanned"
                    result["notes"] = f"Scanned {step} steps, found {len(all_questions)} questions"

                break

            # Check for Review button (final step before submit)
            review_btn = await find_button(page, ["review", "review application"])
            if review_btn:
                if mode == "apply":
                    await review_btn.click()
                    await random_delay(1, 2)
                continue

            # Check for Next button
            next_btn = await find_button(page, ["next", "continue"])
            if next_btn:
                await next_btn.click()
                await random_delay(1, 2)
                continue

            # No navigation button found — might be stuck
            result["notes"] = f"Got stuck at step {step} — no Next/Submit button found"
            break

        result["questions"] = all_questions

        # Close modal if still open
        await close_modal(page)

    except Exception as e:
        result["notes"] = f"Error: {str(e)[:200]}"

    return result


async def fill_easy_apply_step(page: Page, job: dict) -> List[dict]:
    """Fill one step of the Easy Apply modal. Returns unknown fields."""
    unknown_fields = []

    # Handle resume upload
    await handle_resume_upload(page)

    # Handle cover letter upload (ALWAYS try to attach one)
    await handle_cover_letter_upload(page, job)

    # Fill text/select/radio fields
    unknown = await detect_and_fill_fields(page, job)
    unknown_fields.extend(unknown)

    return unknown_fields


async def handle_resume_upload(page: Page):
    """Upload resume if a file input for resume is present."""
    # Look for resume upload input
    file_inputs = await page.query_selector_all('input[type="file"]')
    for file_input in file_inputs:
        # Check if this is for resume (not cover letter)
        parent = await file_input.evaluate_handle("el => el.closest('div[class*=\"form\"], div[class*=\"upload\"], div')")
        parent_text = ""
        try:
            parent_text = (await parent.inner_text()).lower()
        except Exception:
            pass

        if "resume" in parent_text or "cv" in parent_text:
            # Check if already uploaded
            existing = await page.query_selector('[class*="resume"] [class*="file-name"], [class*="uploaded"]')
            if not existing:
                await file_input.set_input_files(RESUME_PATH)
                print(f"[easy-apply] Uploaded resume")
                await random_delay(1, 2)
            break


async def handle_cover_letter_upload(page: Page, job: dict):
    """Upload cover letter if the option exists. ALWAYS attach one."""
    # Check for cover letter section or upload
    cover_letter_indicators = [
        'input[type="file"]',  # second file input often for cover letter
        '[class*="cover-letter"]',
        '[class*="cover_letter"]',
    ]

    # Look for "Add" or "Upload" button for cover letter
    add_cover_btn = None
    buttons = await page.query_selector_all('button, [role="button"]')
    for btn in buttons:
        btn_text = (await btn.inner_text()).strip().lower()
        if "cover letter" in btn_text or ("add" in btn_text and "document" in btn_text):
            add_cover_btn = btn
            break

    if add_cover_btn:
        await add_cover_btn.click()
        await random_delay(0.5, 1)

    # Find cover letter file input
    file_inputs = await page.query_selector_all('input[type="file"]')
    for file_input in file_inputs:
        parent = await file_input.evaluate_handle("el => el.closest('div[class*=\"form\"], div[class*=\"upload\"], div')")
        parent_text = ""
        try:
            parent_text = (await parent.inner_text()).lower()
        except Exception:
            pass

        if "cover" in parent_text or "letter" in parent_text or "additional" in parent_text:
            cover_letter_path = get_cover_letter_pdf_path(job)
            await file_input.set_input_files(cover_letter_path)
            print(f"[easy-apply] Uploaded cover letter for {job.get('company', 'unknown')}")
            await random_delay(1, 2)
            break

    # Also check for a textarea to paste cover letter text
    textareas = await page.query_selector_all('textarea')
    for textarea in textareas:
        label = await textarea.evaluate("""el => {
            const label = el.closest('div')?.querySelector('label, span[class*="label"]');
            return label ? label.innerText : '';
        }""")
        if "cover letter" in label.lower() or "additional information" in label.lower():
            from cover_letter_manager import find_cover_letter_for_job
            letter_text = find_cover_letter_for_job(job)
            if letter_text:
                await textarea.fill(letter_text)
                print(f"[easy-apply] Filled cover letter text field for {job.get('company', 'unknown')}")
            break


async def find_easy_apply_button(page: Page):
    """Find the Easy Apply button on a LinkedIn job page."""
    # Try multiple selectors
    selectors = [
        'button.jobs-apply-button',
        'button[class*="jobs-apply-button"]',
        '[class*="jobs-apply-button"]',
        'button:has-text("Easy Apply")',
    ]
    for selector in selectors:
        btn = await page.query_selector(selector)
        if btn:
            btn_text = (await btn.inner_text()).strip().lower()
            if "easy apply" in btn_text or "apply" in btn_text:
                return btn

    # Fallback: find any button with "Easy Apply" text
    buttons = await page.query_selector_all('button')
    for btn in buttons:
        text = (await btn.inner_text()).strip().lower()
        if "easy apply" in text:
            return btn

    return None


async def find_button(page: Page, texts: list[str]):
    """Find a button by its text content (case-insensitive)."""
    # Try within modal first
    modal = await page.query_selector('[class*="artdeco-modal"], [role="dialog"]')
    container = modal if modal else page

    buttons = await container.query_selector_all('button, [role="button"]')
    for btn in buttons:
        try:
            btn_text = (await btn.inner_text()).strip().lower()
            aria_label = (await btn.get_attribute("aria-label") or "").lower()

            for target_text in texts:
                if target_text in btn_text or target_text in aria_label:
                    # Make sure button is visible and enabled
                    is_visible = await btn.is_visible()
                    is_disabled = await btn.get_attribute("disabled")
                    if is_visible and not is_disabled:
                        return btn
        except Exception:
            continue

    return None


async def check_submission_success(page: Page) -> bool:
    """Check if the application was submitted successfully."""
    try:
        # Look for success indicators
        success_selectors = [
            '[class*="artdeco-inline-feedback--success"]',
            '[class*="post-apply"]',
            'h2:has-text("application was sent")',
            ':text("application was sent")',
            ':text("applied")',
            '[class*="success"]',
        ]
        for selector in success_selectors:
            el = await page.query_selector(selector)
            if el:
                return True

        # Check page text
        page_text = await page.inner_text("body")
        if "application was sent" in page_text.lower():
            return True
        if "you applied" in page_text.lower():
            return True

    except Exception:
        pass

    return False


async def close_modal(page: Page):
    """Close the Easy Apply modal if it's still open."""
    try:
        dismiss_btn = await page.query_selector(
            '[class*="artdeco-modal__dismiss"], button[aria-label="Dismiss"], button[aria-label="Close"]'
        )
        if dismiss_btn:
            await dismiss_btn.click()
            await random_delay(0.5, 1)

            # Handle "Discard" confirmation if it pops up
            discard_btn = await page.query_selector('button:has-text("Discard")')
            if discard_btn:
                await discard_btn.click()
    except Exception:
        pass
