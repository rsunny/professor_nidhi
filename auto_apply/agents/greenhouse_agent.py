"""Greenhouse Form Agent — programmatic fill for Greenhouse job applications.

Greenhouse has PREDICTABLE field IDs and structure — no AI needed for standard fields.
Only uses AI (via lookup_answer) for custom company-specific questions.

Model: None for standard fields, haiku for custom questions
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from playwright.async_api import Page

from . import (
    AgentResult, execute_lookup, set_current_job,
    fill_element_by_index, select_element_by_index, click_element_by_index,
    upload_file_by_index, get_interactive_elements, check_success_indicators,
    random_delay, RESUME_PATH,
)
from profile_tools import _load_profile


# ---------------------------------------------------------------------------
# Known Greenhouse field mappings
# ---------------------------------------------------------------------------

# Standard Greenhouse fields → profile answers
GREENHOUSE_FIELD_MAP = {
    "first_name": "Nidhi",
    "last_name": "Shetty",
    "email": None,  # Will load from profile
    "phone": None,
    "resume": "upload",
    "cover_letter": "upload",
    "linkedin_profile": None,
    "website": "",
    "location": "London, UK",
    "how_did_you_hear": "LinkedIn",
}


def _get_profile_value(field_key: str) -> str:
    """Get value for a Greenhouse field from profile."""
    profile = _load_profile()
    personal = profile.get("personal", {})

    mapping = {
        "first_name": personal.get("first_name", "Nidhi"),
        "last_name": personal.get("last_name", "Shetty"),
        "email": personal.get("email", ""),
        "phone": personal.get("phone", ""),
        "linkedin_profile": personal.get("linkedin_url", ""),
        "location": personal.get("location", "London, UK"),
        "city": personal.get("city", "London"),
        "how_did_you_hear": "LinkedIn",
    }
    return mapping.get(field_key, "")


# ---------------------------------------------------------------------------
# Main Greenhouse form filler
# ---------------------------------------------------------------------------

async def fill_greenhouse_form(page: Page, job: dict, resume_path: str,
                                cover_letter_path: str = "") -> AgentResult:
    """Fill a Greenhouse application form programmatically.

    Strategy:
    1. Fill known fields by their IDs/names
    2. Upload resume & cover letter
    3. Handle custom questions with AI lookup
    4. Click Submit
    """
    set_current_job(job)
    print("    [greenhouse] Filling form programmatically...")

    fields_filled = 0

    # Step 1: Fill standard fields by common selectors
    standard_fields = [
        ('input#first_name, input[name="job_application[first_name]"]', "Nidhi"),
        ('input#last_name, input[name="job_application[last_name]"]', "Shetty"),
        ('input#email, input[name="job_application[email]"]', _get_profile_value("email")),
        ('input#phone, input[name="job_application[phone]"]', _get_profile_value("phone")),
        ('input[name*="linkedin"], input[id*="linkedin"]', _get_profile_value("linkedin_profile")),
        ('input[name*="website"], input[id*="website"]', _get_profile_value("linkedin_profile")),
    ]

    for selector, value in standard_fields:
        if not value:
            continue
        try:
            field = page.locator(selector).first
            if await field.is_visible(timeout=1500):
                current_val = await field.input_value()
                if not current_val.strip():
                    await field.fill(value)
                    fields_filled += 1
                    await random_delay(0.2, 0.5)
        except Exception:
            continue

    # Step 1b: Handle location autocomplete field (Greenhouse uses typeahead)
    try:
        location_field = page.locator(
            'input[name*="location"], input[id*="location"], '
            'input[placeholder*="location" i], input[placeholder*="city" i], '
            'input[aria-label*="location" i], input[autocomplete="address-level2"]'
        ).first
        if await location_field.is_visible(timeout=2000):
            current_val = await location_field.input_value()
            if not current_val.strip():
                await location_field.click()
                await random_delay(0.3, 0.5)
                await location_field.fill("London")
                await random_delay(1, 2)  # Wait for autocomplete suggestions

                # Try to click first suggestion in dropdown
                suggestion_selectors = [
                    'li[role="option"]', '.pac-item', '[class*="suggestion"]',
                    '[class*="autocomplete"] li', '[class*="dropdown"] li',
                    'ul[role="listbox"] li', '.location-autocomplete-results li',
                ]
                clicked_suggestion = False
                for sel in suggestion_selectors:
                    try:
                        suggestion = page.locator(sel).first
                        if await suggestion.is_visible(timeout=2000):
                            text = await suggestion.inner_text()
                            if "london" in text.lower():
                                await suggestion.click()
                                clicked_suggestion = True
                                fields_filled += 1
                                break
                    except Exception:
                        continue

                if not clicked_suggestion:
                    # No suggestions found — try pressing Enter or just leave "London"
                    await page.keyboard.press("Enter")
                    await random_delay(0.5, 1)
                    # If field still says "London", append ", UK"
                    try:
                        val = await location_field.input_value()
                        if val == "London":
                            await location_field.fill("London, UK")
                    except Exception:
                        pass
                    fields_filled += 1
    except Exception:
        pass

    # Step 2: Upload resume
    try:
        resume_input = page.locator(
            'input[type="file"][id*="resume"], input[type="file"][name*="resume"], '
            'input[type="file"]:first-of-type'
        ).first
        if await resume_input.is_visible(timeout=2000):
            await resume_input.set_input_files(resume_path)
            fields_filled += 1
            print("    [greenhouse] Resume uploaded")
            await random_delay(1, 2)
    except Exception:
        # Try via button click + file chooser
        try:
            upload_btn = page.locator(
                'button:has-text("Attach"), a:has-text("Attach"), '
                'label:has-text("Resume"), label:has-text("CV")'
            ).first
            if await upload_btn.is_visible(timeout=2000):
                async with page.expect_file_chooser(timeout=5000) as fc:
                    await upload_btn.click()
                chooser = await fc.value
                await chooser.set_files(resume_path)
                fields_filled += 1
                print("    [greenhouse] Resume uploaded via button")
        except Exception:
            pass

    # Step 3: Upload cover letter
    if cover_letter_path and Path(cover_letter_path).exists():
        try:
            cl_input = page.locator(
                'input[type="file"][id*="cover"], input[type="file"][name*="cover"]'
            ).first
            if await cl_input.is_visible(timeout=2000):
                await cl_input.set_input_files(cover_letter_path)
                fields_filled += 1
                print("    [greenhouse] Cover letter uploaded")
        except Exception:
            pass

    # Step 4: Handle "How did you hear about us" dropdown
    try:
        source_select = page.locator(
            'select[id*="source"], select[name*="source"], '
            'select[id*="hear"], select[name*="referral"]'
        ).first
        if await source_select.is_visible(timeout=1500):
            try:
                await source_select.select_option(label="LinkedIn")
            except Exception:
                try:
                    await source_select.select_option(value="LinkedIn")
                except Exception:
                    # Try partial match
                    options = await source_select.locator("option").all_text_contents()
                    for opt in options:
                        if "linkedin" in opt.lower():
                            await source_select.select_option(label=opt)
                            break
            fields_filled += 1
    except Exception:
        pass

    # Step 5: Handle custom questions (company-specific)
    # These are typically in divs with class "field" and have varied structures
    custom_handled = await _handle_custom_questions(page)
    fields_filled += custom_handled

    # Step 6: Handle EEO/demographic questions (checkboxes and selects)
    await _handle_eeo_questions(page)

    # Step 7: Submit
    await random_delay(1, 2)
    submit_selectors = [
        'button#submit_app', 'button[type="submit"]',
        'input[type="submit"]', 'button:has-text("Submit Application")',
        'button:has-text("Submit")', 'button:has-text("Apply")',
    ]

    for sel in submit_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await random_delay(3, 5)

                # Check for success
                if await check_success_indicators(page):
                    return AgentResult(success=True, status="applied",
                                       data={"fields_filled": fields_filled, "reason": "Greenhouse form submitted"})
                # Check if we're still on the form (validation errors)
                break
        except Exception:
            continue

    # Final success check
    await random_delay(2, 3)
    if await check_success_indicators(page):
        return AgentResult(success=True, status="applied",
                           data={"fields_filled": fields_filled, "reason": "Success page detected"})

    # Check for validation errors
    try:
        errors = await page.locator('.field-error, .error-message, [class*="error"]').all_text_contents()
        if errors:
            error_text = "; ".join(e.strip() for e in errors if e.strip())[:200]
            return AgentResult(success=False, status="validation_error",
                               error=f"Form errors: {error_text}",
                               data={"fields_filled": fields_filled})
    except Exception:
        pass

    return AgentResult(success=False, status="no_submit",
                       error="Could not confirm submission",
                       data={"fields_filled": fields_filled})


# ---------------------------------------------------------------------------
# Custom question handling (uses AI for unknown questions)
# ---------------------------------------------------------------------------

async def _handle_custom_questions(page: Page) -> int:
    """Handle custom/company-specific questions on Greenhouse forms.
    Returns number of fields handled.
    """
    filled = 0
    try:
        # Find custom question containers
        # Greenhouse wraps custom Qs in divs with specific patterns
        custom_fields = await page.evaluate("""() => {
            const results = [];
            // Look for text inputs that aren't standard fields
            const inputs = document.querySelectorAll(
                'input[type="text"]:not(#first_name):not(#last_name):not(#email):not(#phone), ' +
                'textarea:not([name*="cover"]), select:not([id*="source"])'
            );
            for (const input of inputs) {
                const rect = input.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                // Find label
                let label = '';
                const labelEl = input.closest('.field, .form-group, .question')
                    ?.querySelector('label, .field-label, legend');
                if (labelEl) label = labelEl.innerText.trim();
                if (!label) {
                    const id = input.id;
                    if (id) {
                        const labelFor = document.querySelector(`label[for="${id}"]`);
                        if (labelFor) label = labelFor.innerText.trim();
                    }
                }
                if (!label) label = input.placeholder || input.name || '';
                if (label && !input.value) {
                    results.push({
                        label: label.substring(0, 200),
                        tag: input.tagName.toLowerCase(),
                        type: input.type || 'text',
                        id: input.id || '',
                        name: input.name || '',
                    });
                }
            }
            return results;
        }""")

        for field_info in custom_fields[:10]:  # Cap at 10 custom questions
            label = field_info.get("label", "")
            if not label:
                continue

            tag = field_info.get("tag", "input")
            field_type = "textarea" if tag == "textarea" else "text"
            if tag == "select":
                field_type = "select"

            # Get answer from lookup
            answer_json = execute_lookup(label, field_type)
            try:
                answer_data = json.loads(answer_json)
                answer = answer_data.get("answer", "")
                if answer and answer != "UNKNOWN":
                    # Fill the field
                    field_id = field_info.get("id", "")
                    field_name = field_info.get("name", "")

                    selector = ""
                    if field_id:
                        selector = f'#{field_id}'
                    elif field_name:
                        selector = f'[name="{field_name}"]'

                    if selector:
                        try:
                            el = page.locator(selector).first
                            if await el.is_visible(timeout=1000):
                                if tag == "select":
                                    await el.select_option(label=answer)
                                else:
                                    await el.fill(answer)
                                filled += 1
                                await random_delay(0.2, 0.4)
                        except Exception:
                            pass
            except Exception:
                pass

    except Exception:
        pass

    return filled


async def _handle_eeo_questions(page: Page):
    """Handle Equal Employment Opportunity questions (demographic, voluntary)."""
    # Gender
    try:
        gender_select = page.locator('select[id*="gender"], select[name*="gender"]').first
        if await gender_select.is_visible(timeout=1000):
            await gender_select.select_option(label="Female")
    except Exception:
        pass

    # Ethnicity
    try:
        race_select = page.locator('select[id*="race"], select[id*="ethnic"], select[name*="race"]').first
        if await race_select.is_visible(timeout=1000):
            options = await race_select.locator("option").all_text_contents()
            for opt in options:
                if "asian" in opt.lower() or "indian" in opt.lower():
                    await race_select.select_option(label=opt)
                    break
            else:
                # Select "Decline" or "Prefer not to say" if available
                for opt in options:
                    if "decline" in opt.lower() or "prefer not" in opt.lower():
                        await race_select.select_option(label=opt)
                        break
    except Exception:
        pass

    # Veteran status
    try:
        vet_select = page.locator('select[id*="veteran"], select[name*="veteran"]').first
        if await vet_select.is_visible(timeout=1000):
            options = await vet_select.locator("option").all_text_contents()
            for opt in options:
                if "not" in opt.lower() or "no" in opt.lower():
                    await vet_select.select_option(label=opt)
                    break
    except Exception:
        pass

    # Disability
    try:
        dis_select = page.locator('select[id*="disab"], select[name*="disab"]').first
        if await dis_select.is_visible(timeout=1000):
            options = await dis_select.locator("option").all_text_contents()
            for opt in options:
                if "no" in opt.lower() and "disab" not in opt.lower():
                    await dis_select.select_option(label=opt)
                    break
                if "prefer not" in opt.lower() or "decline" in opt.lower():
                    await dis_select.select_option(label=opt)
                    break
    except Exception:
        pass
