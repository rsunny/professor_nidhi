"""Lever Form Agent — handles Lever.co job application forms.

Lever forms are typically SINGLE PAGE with a clean structure:
- Standard fields at top (name, email, phone, resume, LinkedIn)
- Custom questions below
- Single Submit button

Mostly programmatic like Greenhouse, with AI for custom questions.
Model: None for standard, haiku for custom questions
"""

from __future__ import annotations

import json
from pathlib import Path
from playwright.async_api import Page

from . import (
    AgentResult, execute_lookup, set_current_job,
    check_success_indicators, random_delay, RESUME_PATH,
)
from profile_tools import _load_profile


async def fill_lever_form(page: Page, job: dict, resume_path: str,
                           cover_letter_path: str = "") -> AgentResult:
    """Fill a Lever.co application form.

    Lever forms have predictable structure:
    - #resume-upload (file input)
    - input[name="name"] or individual first/last
    - input[name="email"]
    - input[name="phone"]
    - input[name="urls[LinkedIn]"] or similar
    - Custom cards with text/textarea/select
    """
    set_current_job(job)
    profile = _load_profile()
    personal = profile.get("personal", {})
    print("    [lever] Filling form...")

    fields_filled = 0

    # Standard Lever fields
    field_data = [
        ('input[name="name"]', personal.get("full_name", "Nidhi Shetty")),
        ('input[name="email"]', personal.get("email", "")),
        ('input[name="phone"]', personal.get("phone", "")),
        ('input[name*="linkedin" i], input[name*="LinkedIn"]', personal.get("linkedin_url", "")),
        ('input[name*="url" i]:not([name*="linkedin" i])', personal.get("linkedin_url", "")),
        ('input[name="org"]', "Morgan Stanley"),
        ('input[name*="location" i]', personal.get("location", "London, UK")),
        ('input[name*="company" i]', "Morgan Stanley"),
        ('input[name*="school" i]', "University of Westminster"),
    ]

    for selector, value in field_data:
        if not value:
            continue
        try:
            field = page.locator(selector).first
            if await field.is_visible(timeout=1500):
                current = await field.input_value()
                if not current.strip():
                    await field.fill(value)
                    fields_filled += 1
                    await random_delay(0.2, 0.4)
        except Exception:
            continue

    # Resume upload
    try:
        # Lever uses a specific upload area
        file_input = page.locator('input[type="file"][name="resume"], input[type="file"]').first
        if await file_input.count() > 0:
            await file_input.set_input_files(resume_path)
            fields_filled += 1
            print("    [lever] Resume uploaded")
            await random_delay(1, 2)
    except Exception:
        # Try drag-and-drop style
        try:
            upload_area = page.locator('.resume-upload, .upload-area, [class*="upload"]').first
            if await upload_area.is_visible(timeout=2000):
                file_input = page.locator('input[type="file"]').first
                await file_input.set_input_files(resume_path)
                fields_filled += 1
        except Exception:
            pass

    # Cover letter (Lever sometimes has a separate text area or file input)
    if cover_letter_path and Path(cover_letter_path).exists():
        try:
            cl_input = page.locator('input[type="file"][name*="cover" i]').first
            if await cl_input.is_visible(timeout=1500):
                await cl_input.set_input_files(cover_letter_path)
                fields_filled += 1
        except Exception:
            pass

    # How did you hear about us
    try:
        source_field = page.locator(
            'select[name*="source" i], select[name*="hear" i], '
            'input[name*="source" i]'
        ).first
        if await source_field.is_visible(timeout=1500):
            tag = await source_field.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                try:
                    await source_field.select_option(label="LinkedIn")
                except Exception:
                    options = await source_field.locator("option").all_text_contents()
                    for opt in options:
                        if "linkedin" in opt.lower():
                            await source_field.select_option(label=opt)
                            break
            else:
                await source_field.fill("LinkedIn")
            fields_filled += 1
    except Exception:
        pass

    # Custom questions (Lever puts these in "application-question" divs)
    custom_count = await _fill_lever_custom_questions(page)
    fields_filled += custom_count

    # Submit
    await random_delay(1, 2)
    submit_selectors = [
        'button[type="submit"]', 'button:has-text("Submit Application")',
        'button:has-text("Submit")', 'input[type="submit"]',
        'a.postings-btn.template-btn-submit',
    ]

    for sel in submit_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await random_delay(3, 5)
                break
        except Exception:
            continue

    # Check success
    if await check_success_indicators(page):
        return AgentResult(success=True, status="applied",
                           data={"fields_filled": fields_filled, "reason": "Lever form submitted"})

    # Check for thank you page (Lever redirects to /thanks)
    if "/thanks" in page.url or "/thank" in page.url:
        return AgentResult(success=True, status="applied",
                           data={"fields_filled": fields_filled, "reason": "Redirected to thanks page"})

    # Check for errors
    try:
        errors = await page.locator('.error, .field-error, [class*="error"]').all_text_contents()
        if errors:
            error_text = "; ".join(e.strip() for e in errors if e.strip())[:200]
            return AgentResult(success=False, status="validation_error",
                               error=f"Form errors: {error_text}",
                               data={"fields_filled": fields_filled})
    except Exception:
        pass

    return AgentResult(success=False, status="no_confirmation",
                       error="Could not confirm submission",
                       data={"fields_filled": fields_filled})


async def _fill_lever_custom_questions(page: Page) -> int:
    """Fill custom questions in Lever forms. Returns count filled."""
    filled = 0
    try:
        # Lever custom questions are in specific containers
        questions = await page.evaluate("""() => {
            const results = [];
            const containers = document.querySelectorAll(
                '.application-question, .custom-question, [class*="question"]'
            );
            for (const c of containers) {
                const label = c.querySelector('label, .question-label, legend');
                const input = c.querySelector('input:not([type="hidden"]), textarea, select');
                if (label && input) {
                    const rect = input.getBoundingClientRect();
                    if (rect.width === 0) continue;
                    results.push({
                        label: label.innerText.trim().substring(0, 200),
                        tag: input.tagName.toLowerCase(),
                        type: input.type || 'text',
                        id: input.id || '',
                        name: input.name || '',
                        value: input.value || '',
                    });
                }
            }
            // Also check for textareas and inputs without standard names
            const loose = document.querySelectorAll(
                'textarea:not([name="comments"]), ' +
                'input[type="text"]:not([name="name"]):not([name="email"]):not([name="phone"])'
            );
            for (const input of loose) {
                const rect = input.getBoundingClientRect();
                if (rect.width === 0 || input.value) continue;
                let label = '';
                const parent = input.closest('.field, .form-group, li');
                if (parent) {
                    const lbl = parent.querySelector('label');
                    if (lbl) label = lbl.innerText.trim();
                }
                if (!label) label = input.placeholder || input.name || '';
                if (label && !results.find(r => r.label === label)) {
                    results.push({
                        label: label.substring(0, 200),
                        tag: input.tagName.toLowerCase(),
                        type: input.type || 'text',
                        id: input.id || '',
                        name: input.name || '',
                        value: '',
                    });
                }
            }
            return results;
        }""")

        for q in questions[:10]:
            label = q.get("label", "")
            if not label or q.get("value"):
                continue

            field_type = "textarea" if q["tag"] == "textarea" else ("select" if q["tag"] == "select" else "text")

            answer_json = execute_lookup(label, field_type)
            try:
                answer_data = json.loads(answer_json)
                answer = answer_data.get("answer", "")
                if answer and answer != "UNKNOWN":
                    selector = ""
                    if q["id"]:
                        selector = f'#{q["id"]}'
                    elif q["name"]:
                        selector = f'[name="{q["name"]}"]'

                    if selector:
                        el = page.locator(selector).first
                        if await el.is_visible(timeout=1000):
                            if q["tag"] == "select":
                                try:
                                    await el.select_option(label=answer)
                                except Exception:
                                    pass
                            else:
                                await el.fill(answer)
                            filled += 1
                            await random_delay(0.2, 0.4)
            except Exception:
                pass

    except Exception:
        pass

    return filled
