"""External ATS form filler — handles Workday, Greenhouse, Lever, etc.
Supports unlimited page depth — will keep clicking Next/Continue until submission."""

import asyncio
import re
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
    get_answers,
)
from humanizer import random_delay, random_mouse_move


def detect_ats(url: str) -> str:
    """Detect ATS system from URL pattern."""
    url_lower = url.lower()
    if "workday" in url_lower or "myworkday" in url_lower:
        return "workday"
    if "greenhouse.io" in url_lower or "boards.greenhouse" in url_lower:
        return "greenhouse"
    if "lever.co" in url_lower or "jobs.lever" in url_lower:
        return "lever"
    if "smartrecruiters" in url_lower:
        return "smartrecruiters"
    if "icims" in url_lower:
        return "icims"
    if "taleo" in url_lower:
        return "taleo"
    if "successfactors" in url_lower:
        return "successfactors"
    if "reed.co.uk" in url_lower:
        return "reed"
    return "unknown"


async def handle_external_apply(
    page: Page, job: dict, cover_letter_path: Path = None
) -> str:
    """
    Handle external application. Detects ATS and delegates to appropriate handler.
    Returns: "applied", "failed", "skipped", "review"
    """
    job_url = job["url"]
    job_id = job["id"]
    company = job.get("company", "Unknown")

    try:
        # For LinkedIn jobs, first navigate to the job and click Apply
        if "linkedin.com" in job_url:
            await page.goto(job_url, wait_until="domcontentloaded")
            await random_delay(10, 20)

            # Click the Apply button (which opens external link)
            apply_btn = page.locator(
                'button:has-text("Apply"), a:has-text("Apply")'
            ).first

            if await apply_btn.is_visible(timeout=5000):
                # This might open a new tab
                async with page.context.expect_page() as new_page_info:
                    await apply_btn.click()
                    await random_delay(5, 10)

                try:
                    new_page = await new_page_info.value
                    await new_page.wait_for_load_state("domcontentloaded")
                    page = new_page  # Work on the new tab
                    await random_delay(8, 15)
                except Exception:
                    # Didn't open new tab, might have navigated
                    await random_delay(8, 15)
        else:
            # Direct external URL (e.g., Reed jobs)
            await page.goto(job_url, wait_until="domcontentloaded")
            await random_delay(10, 20)

        current_url = page.url
        ats = detect_ats(current_url)
        print(f"    🔗 External application — ATS: {ats}")

        # Route to appropriate handler
        if ats == "workday":
            return await handle_workday(page, job, cover_letter_path)
        elif ats == "greenhouse":
            return await handle_greenhouse(page, job, cover_letter_path)
        elif ats == "lever":
            return await handle_lever(page, job, cover_letter_path)
        elif ats == "smartrecruiters":
            return await handle_smartrecruiters(page, job, cover_letter_path)
        elif ats == "reed":
            return await handle_reed(page, job, cover_letter_path)
        else:
            return await handle_generic_form(page, job, cover_letter_path)

    except PlaywrightTimeout:
        print(f"  ❌ Timeout on external application for job #{job_id}")
        await take_screenshot(page, job_id, "external_timeout")
        return "failed"
    except Exception as e:
        print(f"  ❌ External application error for job #{job_id}: {e}")
        await take_screenshot(page, job_id, "external_error")
        return "failed"


async def handle_workday(page: Page, job: dict, cover_letter_path: Path) -> str:
    """Handle Workday applications — multi-page, unlimited depth."""
    answers = get_answers()
    personal = answers["personal"]
    max_pages = 30  # Safety limit

    for page_num in range(max_pages):
        await random_delay(8, 15)
        print(f"    📄 Workday page {page_num + 1}")

        # Fill all visible form fields
        await fill_all_visible_fields(page, cover_letter_path)

        # Handle file uploads
        await handle_file_uploads(page, cover_letter_path)

        # Look for Next/Continue/Submit button
        submit_btn = page.locator(
            'button:has-text("Submit"), '
            'button[data-automation-id="bottom-navigation-next-button"]'
        ).first

        next_btn = page.locator(
            'button:has-text("Next"), '
            'button:has-text("Continue"), '
            'button:has-text("Save and Continue"), '
            'button[data-automation-id="bottom-navigation-next-button"]'
        ).first

        # Check if this is the final submit page
        page_text = (await page.inner_text("body")).lower()
        if "review" in page_text and "submit" in page_text:
            if MODE == "review":
                print(f"    👁️  Workday — ready for review (job #{job['id']})")
                await take_screenshot(page, job["id"], "workday_review")
                response = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("      Submit? (enter/skip): ")
                )
                if response.strip().lower() == "skip":
                    return "skipped"

            if await submit_btn.is_visible(timeout=3000):
                await submit_btn.click()
                await random_delay(10, 15)
                return "applied"

        # Click Next to go to the next page
        if await next_btn.is_visible(timeout=5000):
            await next_btn.click()
            await random_delay(10, 15)

            # Check for validation errors
            errors = page.locator('[data-automation-id="errorMessage"], .error-message')
            if await errors.count() > 0:
                print(f"    ⚠️  Validation errors on page {page_num + 1}")
                # Try filling again
                await fill_all_visible_fields(page, cover_letter_path)
                await random_delay(3, 5)
                await next_btn.click()
                await random_delay(10, 15)
        else:
            # No next button — check if we're done or stuck
            if "thank" in page_text or "submitted" in page_text or "confirmation" in page_text:
                return "applied"
            print(f"    ⚠️  No Next button on Workday page {page_num + 1}")
            await take_screenshot(page, job["id"], f"workday_stuck_p{page_num}")
            return "failed"

    return "failed"


async def handle_greenhouse(page: Page, job: dict, cover_letter_path: Path) -> str:
    """Handle Greenhouse applications — typically single long page."""
    await random_delay(8, 12)
    print("    🌱 Greenhouse form detected")

    # Greenhouse is typically a single page with all fields
    await fill_all_visible_fields(page, cover_letter_path)
    await handle_file_uploads(page, cover_letter_path)
    await random_delay(5, 8)

    # Submit
    submit_btn = page.locator(
        'button[type="submit"], '
        'input[type="submit"], '
        'button:has-text("Submit Application"), '
        'button:has-text("Submit")'
    ).first

    if await submit_btn.is_visible(timeout=5000):
        if MODE == "review":
            await take_screenshot(page, job["id"], "greenhouse_review")
            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("      Submit Greenhouse app? (enter/skip): ")
            )
            if response.strip().lower() == "skip":
                return "skipped"

        await random_delay(3, 6)
        await submit_btn.click()
        await random_delay(10, 15)

        # Check for success
        page_text = (await page.inner_text("body")).lower()
        if "thank" in page_text or "submitted" in page_text or "received" in page_text:
            return "applied"
        return "applied"  # Optimistic

    return "failed"


async def handle_lever(page: Page, job: dict, cover_letter_path: Path) -> str:
    """Handle Lever applications — usually single page."""
    await random_delay(8, 12)
    print("    🔧 Lever form detected")

    # Click Apply if there's an apply button first
    apply_btn = page.locator('a:has-text("Apply"), button:has-text("Apply")').first
    if await apply_btn.is_visible(timeout=3000):
        await apply_btn.click()
        await random_delay(8, 12)

    await fill_all_visible_fields(page, cover_letter_path)
    await handle_file_uploads(page, cover_letter_path)
    await random_delay(5, 8)

    submit_btn = page.locator(
        'button:has-text("Submit application"), '
        'button:has-text("Submit"), '
        'button[type="submit"]'
    ).first

    if await submit_btn.is_visible(timeout=5000):
        if MODE == "review":
            await take_screenshot(page, job["id"], "lever_review")
            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("      Submit Lever app? (enter/skip): ")
            )
            if response.strip().lower() == "skip":
                return "skipped"

        await random_delay(3, 5)
        await submit_btn.click()
        await random_delay(10, 15)
        return "applied"

    return "failed"


async def handle_smartrecruiters(page: Page, job: dict, cover_letter_path: Path) -> str:
    """Handle SmartRecruiters — multi-step."""
    max_pages = 20
    print("    🧠 SmartRecruiters form detected")

    for page_num in range(max_pages):
        await random_delay(8, 12)
        await fill_all_visible_fields(page, cover_letter_path)
        await handle_file_uploads(page, cover_letter_path)

        # Check for submit
        page_text = (await page.inner_text("body")).lower()
        if "review" in page_text and "submit" in page_text:
            submit_btn = page.locator('button:has-text("Submit")').first
            if await submit_btn.is_visible(timeout=3000):
                if MODE == "review":
                    await take_screenshot(page, job["id"], "sr_review")
                    response = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: input("      Submit? (enter/skip): ")
                    )
                    if response.strip().lower() == "skip":
                        return "skipped"
                await submit_btn.click()
                await random_delay(10, 15)
                return "applied"

        # Next button
        next_btn = page.locator(
            'button:has-text("Next"), button:has-text("Continue")'
        ).first
        if await next_btn.is_visible(timeout=5000):
            await next_btn.click()
            await random_delay(10, 15)
        else:
            if "thank" in page_text or "submitted" in page_text:
                return "applied"
            return "failed"

    return "failed"


async def handle_reed(page: Page, job: dict, cover_letter_path: Path) -> str:
    """Handle Reed.co.uk applications."""
    await random_delay(8, 12)
    print("    📋 Reed.co.uk job detected")

    # Reed usually has an "Apply" button that leads to their own form
    apply_btn = page.locator(
        'a:has-text("Apply"), button:has-text("Apply")'
    ).first

    if await apply_btn.is_visible(timeout=5000):
        await apply_btn.click()
        await random_delay(10, 15)

    # Fill Reed's application form (multi-page possible)
    max_pages = 10
    for page_num in range(max_pages):
        await fill_all_visible_fields(page, cover_letter_path)
        await handle_file_uploads(page, cover_letter_path)
        await random_delay(5, 8)

        # Submit or Next
        submit_btn = page.locator('button:has-text("Submit"), button:has-text("Send")').first
        next_btn = page.locator('button:has-text("Next"), button:has-text("Continue")').first

        if await submit_btn.is_visible(timeout=3000):
            if MODE == "review":
                await take_screenshot(page, job["id"], "reed_review")
                response = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("      Submit Reed app? (enter/skip): ")
                )
                if response.strip().lower() == "skip":
                    return "skipped"
            await submit_btn.click()
            await random_delay(10, 15)
            return "applied"
        elif await next_btn.is_visible(timeout=3000):
            await next_btn.click()
            await random_delay(10, 15)
        else:
            page_text = (await page.inner_text("body")).lower()
            if "thank" in page_text or "submitted" in page_text:
                return "applied"
            return "failed"

    return "failed"


async def handle_generic_form(page: Page, job: dict, cover_letter_path: Path) -> str:
    """Generic form handler for unknown ATS systems. Unlimited page depth."""
    max_pages = 30  # Safety limit
    print("    📝 Generic form handler (unknown ATS)")

    for page_num in range(max_pages):
        await random_delay(8, 15)
        print(f"    📄 Form page {page_num + 1}")

        # Fill all visible form fields
        await fill_all_visible_fields(page, cover_letter_path)
        await handle_file_uploads(page, cover_letter_path)
        await random_delay(5, 8)

        page_text = (await page.inner_text("body")).lower()

        # Check if this is a confirmation/success page
        if any(kw in page_text for kw in ["thank you", "submitted", "confirmation", "received your application"]):
            return "applied"

        # Look for Submit button
        submit_btn = page.locator(
            'button:has-text("Submit"), '
            'input[type="submit"], '
            'button:has-text("Send Application"), '
            'button:has-text("Complete"), '
            'button:has-text("Finish")'
        ).first

        # Look for Next/Continue button
        next_btn = page.locator(
            'button:has-text("Next"), '
            'button:has-text("Continue"), '
            'button:has-text("Save and Continue"), '
            'button:has-text("Proceed"), '
            'a:has-text("Next")'
        ).first

        # Prefer submit if visible on a "review" type page
        if await submit_btn.is_visible(timeout=3000):
            if MODE == "review":
                await take_screenshot(page, job["id"], f"generic_review_p{page_num}")
                response = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("      Submit? (enter/skip): ")
                )
                if response.strip().lower() == "skip":
                    return "skipped"

            await random_delay(3, 5)
            await submit_btn.click()
            await random_delay(10, 15)

            # Verify submission
            new_text = (await page.inner_text("body")).lower()
            if any(kw in new_text for kw in ["thank", "submitted", "confirmation", "received"]):
                return "applied"
            return "applied"  # Optimistic

        elif await next_btn.is_visible(timeout=3000):
            await next_btn.click()
            await random_delay(10, 15)
        else:
            # No navigation buttons — might be stuck
            print(f"    ⚠️  No Next/Submit on page {page_num + 1}")
            await take_screenshot(page, job["id"], f"generic_stuck_p{page_num}")
            return "failed"

    return "failed"


async def fill_all_visible_fields(page: Page, cover_letter_path: Path):
    """Fill all visible form fields on the current page."""
    answers = get_answers()
    personal = answers["personal"]

    # Text inputs
    inputs = await page.locator(
        'input[type="text"]:visible, '
        'input[type="email"]:visible, '
        'input[type="tel"]:visible, '
        'input[type="number"]:visible, '
        'input[type="url"]:visible, '
        'textarea:visible'
    ).all()

    for input_el in inputs:
        try:
            # Skip if already filled
            current = await input_el.input_value()
            if current.strip():
                continue

            # Get label
            label_text = await get_field_label(page, input_el)
            if label_text:
                await fill_text_field(page, input_el, label_text)
                await random_delay(1, 3)
        except Exception:
            continue

    # Dropdowns
    selects = await page.locator("select:visible").all()
    for select in selects:
        try:
            label_text = await get_field_label(page, select)
            if label_text:
                await handle_dropdown(page, select, label_text)
                await random_delay(1, 2)
        except Exception:
            continue

    # Radio buttons
    fieldsets = await page.locator("fieldset:visible").all()
    for fieldset in fieldsets:
        try:
            legend = fieldset.locator("legend, label").first
            if await legend.count() > 0:
                label_text = await legend.inner_text()
                await handle_radio_buttons(page, fieldset, label_text)
                await random_delay(1, 2)
        except Exception:
            continue


async def handle_file_uploads(page: Page, cover_letter_path: Path):
    """Handle all file upload inputs on the page."""
    file_inputs = await page.locator('input[type="file"]:visible').all()

    # Also check for hidden file inputs (common pattern)
    if not file_inputs:
        file_inputs = await page.locator('input[type="file"]').all()

    for file_input in file_inputs:
        try:
            # Determine type from surrounding text
            parent = file_input.locator("xpath=ancestor::div[position()<=3]").first
            parent_text = ""
            try:
                parent_text = (await parent.inner_text()).lower()
            except Exception:
                pass

            # Also check accept attribute
            accept = await file_input.get_attribute("accept") or ""

            if "resume" in parent_text or "cv" in parent_text:
                await upload_resume(page, file_input)
                await random_delay(3, 6)
            elif "cover" in parent_text or "letter" in parent_text:
                if cover_letter_path:
                    await upload_cover_letter(page, file_input, cover_letter_path)
                    await random_delay(3, 6)
            else:
                # Default: upload resume for generic file inputs
                await upload_resume(page, file_input)
                await random_delay(3, 6)
        except Exception:
            continue


async def get_field_label(page: Page, element) -> str:
    """Get the label text for a form field."""
    try:
        # Try label[for=id]
        el_id = await element.get_attribute("id")
        if el_id:
            label = page.locator(f'label[for="{el_id}"]')
            if await label.count() > 0:
                return (await label.first.inner_text()).strip()

        # Try aria-label
        aria = await element.get_attribute("aria-label")
        if aria:
            return aria.strip()

        # Try placeholder
        placeholder = await element.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()

        # Try name attribute
        name = await element.get_attribute("name")
        if name:
            return name.replace("_", " ").replace("-", " ").strip()

        # Try nearby label
        parent = element.locator("xpath=ancestor::div[position()<=2]").first
        label = parent.locator("label").first
        if await label.count() > 0:
            return (await label.inner_text()).strip()

    except Exception:
        pass

    return ""


async def take_screenshot(page: Page, job_id: int, suffix: str):
    """Take a screenshot for debugging."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOTS_DIR / f"job_{job_id}_{suffix}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
    except Exception:
        pass
