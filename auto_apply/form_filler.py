"""Generic intelligent form field detection and filling."""

from __future__ import annotations

import re
from typing import List, Optional
from playwright.async_api import Page, ElementHandle
from config import get_application_answers


# Keywords to match form labels to answers
FIELD_MAPPINGS = [
    # Name fields
    (["full name", "your name", "candidate name"], "personal.full_name"),
    (["first name", "given name", "forename"], "personal.first_name"),
    (["last name", "surname", "family name"], "personal.last_name"),

    # Contact
    (["email", "e-mail"], "personal.email"),
    (["phone", "mobile", "telephone", "contact number"], "personal.phone"),
    (["linkedin"], "personal.linkedin_url"),

    # Location
    (["city", "town"], "personal.city"),
    (["postcode", "zip code", "postal code"], "personal.postcode"),
    (["country"], "personal.country"),
    (["address"], "personal.address"),

    # Work auth
    (["sponsorship", "visa sponsor"], "work_authorization.require_sponsorship"),
    (["right to work", "authorized to work", "work authorization", "legally authorized"], "work_authorization.right_to_work_uk"),
    (["visa type"], "work_authorization.visa_type"),
    (["relocate", "relocation"], "work_authorization.willing_to_relocate"),

    # Employment
    (["notice period"], "employment.notice_period"),
    (["start date", "when can you start", "earliest start", "available from"], "employment.available_start_date"),
    (["current employer", "current company"], "employment.current_employer"),
    (["current.*title", "current.*role", "job title"], "employment.current_job_title"),
    (["reason for leaving", "why are you leaving"], "employment.reason_for_leaving"),

    # Salary
    (["current salary", "current compensation"], "salary.current_salary"),
    (["expected salary", "desired salary", "salary expectation", "desired compensation"], "salary.expected_salary_analyst"),

    # Experience
    (["years.*experience", "how many years", "total experience"], "skills.total_years_experience"),
    (["years.*financial", "financial services experience"], "skills.years_financial_services"),

    # Education
    (["highest.*degree", "level.*education", "qualification"], "education.highest_degree"),
    (["university", "school", "institution"], "education.university"),
    (["graduation", "year.*graduated"], "education.graduation_date"),

    # How did you hear
    (["how did you hear", "where did you find", "source"], "additional.how_did_you_hear"),

    # Screening
    (["criminal", "conviction"], "additional.criminal_convictions"),
    (["background check", "dbs check"], "additional.background_checks"),

    # Equal opportunities
    (["gender", "sex"], "personal.gender"),
    (["ethnic", "race"], "personal.ethnicity"),
    (["disability", "disabled"], "personal.disability"),

    # Nationality
    (["nationality", "citizenship"], "personal.nationality"),
]


def get_nested_value(data: dict, key_path: str) -> str:
    """Get a nested dict value by dot-separated path."""
    keys = key_path.split(".")
    value = data
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key, "")
        else:
            return ""
    return str(value) if value else ""


def match_label_to_answer(label: str, answers: dict) -> Optional[str]:
    """Match a form field label to an answer from our data."""
    label_lower = label.lower().strip()

    for keywords, answer_path in FIELD_MAPPINGS:
        for keyword in keywords:
            if re.search(keyword, label_lower):
                value = get_nested_value(answers, answer_path)
                if value:
                    return value

    # Try common_answers for exact-ish matches
    common = answers.get("common_answers", {})
    for question, answer in common.items():
        if question.lower() in label_lower or label_lower in question.lower():
            return answer

    return None


async def detect_and_fill_fields(page: Page, job: dict = None) -> List[dict]:
    """Detect form fields on the page and fill them.

    Returns list of fields that couldn't be filled (unknown questions).
    """
    answers = get_application_answers()
    unknown_fields = []

    # Find all form groups (label + input pairs)
    form_groups = await page.query_selector_all(
        'div[class*="form"], div[class*="field"], div[class*="question"], '
        '.fb-dash-form-element, .jobs-easy-apply-form-section__grouping'
    )

    if not form_groups:
        # Fallback: find all labels
        form_groups = await page.query_selector_all('label, .form-label, [class*="label"]')

    for group in form_groups:
        try:
            # Get the label text
            label_el = await group.query_selector('label, span[class*="label"], [class*="question"]')
            if not label_el:
                label_text = (await group.inner_text()).strip()
            else:
                label_text = (await label_el.inner_text()).strip()

            if not label_text or len(label_text) > 200:
                continue

            # Find the input field
            input_el = await group.query_selector(
                'input:not([type="hidden"]):not([type="file"]):not([type="submit"]), '
                'textarea, select'
            )

            if not input_el:
                # Try radio buttons / checkboxes
                radio_els = await group.query_selector_all('input[type="radio"], input[type="checkbox"]')
                if radio_els:
                    answer = match_label_to_answer(label_text, answers)
                    if answer:
                        await handle_radio_checkbox(group, radio_els, answer)
                    else:
                        unknown_fields.append({"label": label_text, "type": "radio/checkbox"})
                continue

            input_type = await input_el.get_attribute("type") or "text"
            tag_name = await input_el.evaluate("el => el.tagName.toLowerCase()")

            # Skip file inputs (handled separately)
            if input_type == "file":
                continue

            # Check if already filled
            current_value = await input_el.input_value() if tag_name != "select" else ""
            if current_value and len(current_value) > 1:
                continue

            # Match and fill
            answer = match_label_to_answer(label_text, answers)

            if answer:
                if tag_name == "select":
                    await handle_select(input_el, answer)
                else:
                    await input_el.fill("")
                    await input_el.fill(answer)
                    await page.wait_for_timeout(200)
            else:
                unknown_fields.append({
                    "label": label_text,
                    "type": f"{tag_name}[{input_type}]",
                })

        except Exception as e:
            continue

    return unknown_fields


async def handle_select(select_el: ElementHandle, answer: str):
    """Handle dropdown/select fields by matching option text."""
    options = await select_el.query_selector_all("option")
    answer_lower = answer.lower()

    for option in options:
        option_text = (await option.inner_text()).strip().lower()
        option_value = await option.get_attribute("value") or ""

        # Try exact match first
        if answer_lower == option_text or answer_lower == option_value.lower():
            await select_el.select_option(label=await option.inner_text())
            return

    # Try partial match
    for option in options:
        option_text = (await option.inner_text()).strip().lower()
        if answer_lower in option_text or option_text in answer_lower:
            await select_el.select_option(label=await option.inner_text())
            return

    # Try "yes" matching for boolean questions
    if answer_lower in ("yes", "true", "1"):
        for option in options:
            option_text = (await option.inner_text()).strip().lower()
            if "yes" in option_text:
                await select_el.select_option(label=await option.inner_text())
                return


async def handle_radio_checkbox(group: ElementHandle, elements: list[ElementHandle], answer: str):
    """Handle radio buttons and checkboxes by matching labels to answer."""
    answer_lower = answer.lower()

    for el in elements:
        # Get the label for this specific radio/checkbox
        el_id = await el.get_attribute("id")
        if el_id:
            label = await group.query_selector(f'label[for="{el_id}"]')
            if label:
                label_text = (await label.inner_text()).strip().lower()
                if answer_lower in label_text or label_text in answer_lower:
                    await el.click()
                    return
                # Match "yes"/"no" patterns
                if answer_lower in ("yes", "true") and label_text in ("yes", "true", "y"):
                    await el.click()
                    return
                if answer_lower in ("no", "false") and label_text in ("no", "false", "n"):
                    await el.click()
                    return

        # Try matching by value attribute
        value = (await el.get_attribute("value") or "").lower()
        if answer_lower == value or answer_lower in value:
            await el.click()
            return


async def scan_form_questions(page: Page) -> List[dict]:
    """Scan a page for form questions WITHOUT filling them. Used in scan mode."""
    questions = []

    # Look for all label-like elements
    label_selectors = [
        'label',
        '[class*="question"]',
        '[class*="label"]',
        '.fb-dash-form-element__label',
        '.jobs-easy-apply-form-section__grouping label',
        'span[aria-hidden="true"]',
    ]

    seen_texts = set()
    for selector in label_selectors:
        elements = await page.query_selector_all(selector)
        for el in elements:
            try:
                text = (await el.inner_text()).strip()
                if text and len(text) > 2 and len(text) < 300 and text not in seen_texts:
                    seen_texts.add(text)

                    # Determine field type
                    parent = await el.evaluate_handle("el => el.closest('div')")
                    input_el = await parent.query_selector('input, textarea, select')
                    field_type = "unknown"
                    if input_el:
                        tag = await input_el.evaluate("el => el.tagName.toLowerCase()")
                        inp_type = await input_el.get_attribute("type") or "text"
                        field_type = f"{tag}[{inp_type}]"

                    # Check if we know the answer
                    answers = get_application_answers()
                    known_answer = match_label_to_answer(text, answers)

                    questions.append({
                        "label": text,
                        "type": field_type,
                        "has_answer": known_answer is not None,
                        "answer": known_answer or "UNKNOWN - needs manual input",
                    })
            except Exception:
                continue

    return questions
