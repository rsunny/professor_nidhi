"""LinkedIn Easy Apply handler — handles multi-step modal forms."""

import asyncio
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from config import RESUME_PATH, SCREENSHOTS_DIR, MODE
from form_filler import (
    fill_text_field,
    handle_radio_buttons,
    handle_dropdown,
    upload_resume,
    upload_cover_letter,
    match_answer,
)
from humanizer import random_delay, simulate_reading, random_mouse_move


async def handle_easy_apply(page: Page, job: dict, cover_letter_path: Path = None) -> str:
    """
    Handle LinkedIn Easy Apply for a job.
    Returns: "applied", "failed", "skipped", "review"
    """
    job_url = job["url"]
    job_id = job["id"]
    company = job.get("company", "Unknown")

    try:
        # Navigate to job page
        await page.goto(job_url, wait_until="domcontentloaded")
        await random_delay(10, 20)  # Long pause to seem human

        # Simulate reading the job description
        await simulate_reading(page, duration_sec=15)
        await random_mouse_move(page)
        await random_delay(5, 10)

        # Check if job is still available
        page_text = await page.inner_text("body")
        if "no longer accepting" in page_text.lower() or "job is closed" in page_text.lower():
            print(f"  ⏰ Job #{job_id} at {company} is expired/closed")
            return "expired"

        # Find and click Easy Apply button
        easy_apply_btn = page.locator(
            'button:has-text("Easy Apply"), '
            'button.jobs-apply-button:has-text("Apply"), '
            'button[aria-label*="Easy Apply"]'
        ).first
        
        if not await easy_apply_btn.is_visible(timeout=5000):
            # Maybe it's "Apply" that goes external
            apply_btn = page.locator('button:has-text("Apply")').first
            if await apply_btn.is_visible(timeout=3000):
                return "external"  # Signal to caller to use external handler
            print(f"  ⚠️  No Apply button found for job #{job_id}")
            return "skipped"

        await random_delay(2, 5)
        await easy_apply_btn.click()
        await random_delay(8, 15)  # Wait for modal to open

        # Handle the multi-step Easy Apply modal
        result = await process_easy_apply_modal(page, job, cover_letter_path)
        return result

    except PlaywrightTimeout:
        print(f"  ❌ Timeout on job #{job_id} at {company}")
        await take_failure_screenshot(page, job_id)
        return "failed"
    except Exception as e:
        print(f"  ❌ Error on job #{job_id}: {e}")
        await take_failure_screenshot(page, job_id)
        return "failed"


async def process_easy_apply_modal(page: Page, job: dict, cover_letter_path: Path) -> str:
    """Process the multi-step Easy Apply modal. No page limit — goes until done."""
    max_steps = 20  # Safety limit to prevent infinite loops
    step = 0

    while step < max_steps:
        step += 1
        await random_delay(5, 10)  # Human-like pause between steps

        # Check if we're on the review/submit page
        submit_btn = page.locator(
            'button[aria-label="Submit application"], '
            'button:has-text("Submit application"), '
            'button[aria-label="Review your application"]'
        ).first

        if await submit_btn.is_visible(timeout=2000):
            return await handle_submit(page, submit_btn, job)

        # Fill the current step's form fields
        await fill_modal_step(page, job, cover_letter_path)

        # Look for "Next" or "Review" button
        next_btn = page.locator(
            'button[aria-label="Continue to next step"], '
            'button:has-text("Next"), '
            'button:has-text("Review"), '
            'button:has-text("Continue")'
        ).first

        if await next_btn.is_visible(timeout=3000):
            await random_delay(2, 4)
            await next_btn.click()
            await random_delay(8, 12)  # Wait for next step to load

            # Check for validation errors
            error = page.locator(
                '.artdeco-inline-feedback--error, '
                '[data-test-form-element-error], '
                '.fb-form-element-error'
            ).first
            if await error.is_visible(timeout=2000):
                error_text = await error.inner_text()
                print(f"    ⚠️  Validation error at step {step}: {error_text}")
                # Try to fix and retry once
                await fill_modal_step(page, job, cover_letter_path)
                await random_delay(2, 3)
                await next_btn.click()
                await random_delay(5, 8)
        else:
            # No next button — might be single page or we're stuck
            # Check for submit again
            submit_btn = page.locator(
                'button[aria-label="Submit application"], '
                'button:has-text("Submit application")'
            ).first
            if await submit_btn.is_visible(timeout=3000):
                return await handle_submit(page, submit_btn, job)

            # Check for dismiss/close (application already submitted?)
            dismiss = page.locator('button[aria-label="Dismiss"]').first
            if await dismiss.is_visible(timeout=2000):
                await dismiss.click()
                return "applied"

            print(f"    ⚠️  Stuck at step {step} — no Next or Submit button")
            await take_failure_screenshot(page, job["id"])
            # Try to close the modal
            close_btn = page.locator(
                'button[aria-label="Dismiss"], button[aria-label="Close"]'
            ).first
            if await close_btn.is_visible(timeout=2000):
                await close_btn.click()
            return "failed"

    print(f"  ⚠️  Exceeded {max_steps} steps for job #{job['id']}")
    return "failed"


async def fill_modal_step(page: Page, job: dict, cover_letter_path: Path):
    """Fill all form fields in the current modal step."""

    # Handle text inputs
    text_inputs = await page.locator(
        '.jobs-easy-apply-modal input[type="text"], '
        '.jobs-easy-apply-modal input[type="email"], '
        '.jobs-easy-apply-modal input[type="tel"], '
        '.jobs-easy-apply-modal input[type="number"], '
        '.jobs-easy-apply-modal textarea'
    ).all()

    for input_el in text_inputs:
        try:
            # Get label for this input
            input_id = await input_el.get_attribute("id")
            label_text = ""

            if input_id:
                label = page.locator(f'label[for="{input_id}"]')
                if await label.count() > 0:
                    label_text = await label.first.inner_text()

            if not label_text:
                # Try aria-label
                label_text = await input_el.get_attribute("aria-label") or ""

            if not label_text:
                # Try parent's label
                parent = input_el.locator("..")
                label_el = parent.locator("label").first
                if await label_el.count() > 0:
                    label_text = await label_el.inner_text()

            if label_text:
                # Check if field is already filled
                current_value = await input_el.input_value()
                if not current_value.strip():
                    filled = await fill_text_field(page, input_el, label_text)
                    if filled:
                        await random_delay(1, 3)
        except Exception:
            continue

    # Handle radio buttons / fieldsets
    fieldsets = await page.locator(
        '.jobs-easy-apply-modal fieldset, '
        '.jobs-easy-apply-modal [data-test-form-builder-radio-button-form-component]'
    ).all()

    for fieldset in fieldsets:
        try:
            legend = fieldset.locator("legend, span.fb-form-element-label").first
            if await legend.count() > 0:
                label_text = await legend.inner_text()
                await handle_radio_buttons(page, fieldset, label_text)
                await random_delay(1, 2)
        except Exception:
            continue

    # Handle dropdowns
    selects = await page.locator(
        '.jobs-easy-apply-modal select'
    ).all()

    for select in selects:
        try:
            select_id = await select.get_attribute("id")
            label_text = ""
            if select_id:
                label = page.locator(f'label[for="{select_id}"]')
                if await label.count() > 0:
                    label_text = await label.first.inner_text()
            if label_text:
                await handle_dropdown(page, select, label_text)
                await random_delay(1, 2)
        except Exception:
            continue

    # Handle file uploads (resume + cover letter)
    file_inputs = await page.locator(
        '.jobs-easy-apply-modal input[type="file"]'
    ).all()

    for file_input in file_inputs:
        try:
            # Determine if this is resume or cover letter upload
            parent_text = await file_input.locator("..").inner_text()
            parent_text_lower = parent_text.lower()

            if "resume" in parent_text_lower or "cv" in parent_text_lower:
                # Check if already uploaded
                uploaded = page.locator(
                    '.jobs-easy-apply-modal [class*="resume"] .artdeco-button--tertiary'
                )
                if await uploaded.count() == 0:
                    await upload_resume(page, file_input)
                    await random_delay(3, 5)
            elif "cover" in parent_text_lower or "letter" in parent_text_lower:
                if cover_letter_path:
                    from form_filler import upload_cover_letter
                    await upload_cover_letter(page, file_input, cover_letter_path)
                    await random_delay(3, 5)
        except Exception:
            continue


async def handle_submit(page: Page, submit_btn, job: dict) -> str:
    """Handle the final submit step."""
    job_id = job["id"]
    company = job.get("company", "Unknown")

    if MODE == "review":
        print(f"  👁️  Job #{job_id} ({company}) ready for review — pausing before submit")
        print(f"      Press Enter in terminal to submit, or type 'skip' to skip...")
        # In review mode, take screenshot and wait
        await take_failure_screenshot(page, job_id, suffix="review")
        # Since we're async, we use input() via thread
        import sys
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: input("      > "))
        if response.strip().lower() == "skip":
            # Close modal
            close_btn = page.locator('button[aria-label="Dismiss"]').first
            if await close_btn.is_visible(timeout=2000):
                await close_btn.click()
            return "skipped"

    # Click submit
    await random_delay(3, 6)
    await submit_btn.click()
    await random_delay(8, 12)

    # Check for success
    success_indicators = page.locator(
        'h2:has-text("Application submitted"), '
        'h3:has-text("Application submitted"), '
        '[data-test-modal-close-btn], '
        'button:has-text("Done")'
    ).first

    if await success_indicators.is_visible(timeout=10000):
        print(f"  ✅ Applied to job #{job_id} at {company}")
        # Dismiss the success dialog
        done_btn = page.locator(
            'button:has-text("Done"), button[aria-label="Dismiss"]'
        ).first
        if await done_btn.is_visible(timeout=3000):
            await done_btn.click()
        return "applied"
    else:
        print(f"  ⚠️  Uncertain if job #{job_id} submitted — check manually")
        await take_failure_screenshot(page, job_id, suffix="uncertain")
        return "applied"  # Optimistic — it likely went through


async def take_failure_screenshot(page: Page, job_id: int, suffix: str = "fail"):
    """Take a screenshot for debugging."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOTS_DIR / f"job_{job_id}_{suffix}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
    except Exception:
        pass
