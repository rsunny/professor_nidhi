"""Generic intelligent form field detection and filling."""

import re
from pathlib import Path

from playwright.async_api import Page, Locator

from config import RESUME_PATH, load_answers
from humanizer import random_delay


# Load answers once
_answers = None


def get_answers() -> dict:
    global _answers
    if _answers is None:
        _answers = load_answers()
    return _answers


def match_answer(label_text: str) -> str | None:
    """Match a form field label to an answer using pattern matching."""
    answers = get_answers()
    label_lower = label_text.lower().strip()

    # Check question_patterns first (regex-based matching)
    for pattern, answer in answers.get("question_patterns", {}).items():
        if re.search(pattern, label_lower, re.IGNORECASE):
            return answer

    # Direct field matching
    personal = answers.get("personal", {})
    employment = answers.get("employment", {})
    salary = answers.get("salary", {})
    work_auth = answers.get("work_authorization", {})
    skills = answers.get("skills", {})

    # Name fields
    if re.search(r"\bfirst\s*name\b", label_lower):
        return personal.get("first_name")
    if re.search(r"\blast\s*name\b|surname|family\s*name", label_lower):
        return personal.get("last_name")
    if re.search(r"\bfull\s*name\b|\bname\b", label_lower) and "company" not in label_lower:
        return personal.get("full_name")

    # Contact
    if re.search(r"\bemail\b", label_lower):
        return personal.get("email")
    if re.search(r"\bphone\b|\bmobile\b|\btelephone\b", label_lower):
        return personal.get("phone")

    # Location
    if re.search(r"\bpost\s*code\b|\bzip\b", label_lower):
        return personal.get("postcode")
    if re.search(r"\bcity\b|\blocation\b", label_lower):
        return personal.get("city")
    if re.search(r"\baddress\b", label_lower):
        return personal.get("address")

    # LinkedIn
    if re.search(r"\blinkedin\b", label_lower):
        return personal.get("linkedin_url")

    # Work authorization
    if re.search(r"\bsponsorship\b|\bvisa\b|\bauthori[sz]ation\b|\bright to work\b", label_lower):
        return work_auth.get("require_sponsorship")

    # Notice period
    if re.search(r"\bnotice\b", label_lower):
        return employment.get("notice_period")

    # Salary
    if re.search(r"\bsalary\b|\bcompensation\b|\bpay\b|\bpackage\b", label_lower):
        return salary.get("expected_salary_analyst")

    # Experience
    if re.search(r"\byears?\s*(of\s*)?experience\b", label_lower):
        return skills.get("total_years_experience")

    # Start date
    if re.search(r"\bstart\s*date\b|\bavailab", label_lower):
        return employment.get("available_start_date")

    # Currently employed
    if re.search(r"\bcurrently\s*employ", label_lower):
        return employment.get("currently_employed")

    # Employer
    if re.search(r"\bcurrent\s*employer\b|\bcompany\s*name\b", label_lower):
        return employment.get("current_employer")

    return None


def match_yes_no(label_text: str) -> str | None:
    """For yes/no radio buttons, determine the correct answer."""
    answers = get_answers()
    label_lower = label_text.lower()

    # Work authorization → Yes (legally authorized to work in UK)
    if re.search(r"legally\s*authori[sz]ed\s*to\s*work|right\s*to\s*work|eligible\s*to\s*work", label_lower):
        return "Yes"

    # Sponsorship/visa questions → Yes (Nidhi requires sponsorship)
    if re.search(r"sponsorship|require.*visa|visa\s*status", label_lower):
        return "Yes"

    # Comfortable commuting → Yes
    if re.search(r"commut|comfortable.*location|willing.*travel|travel.*office", label_lower):
        return "Yes"

    # Criminal/conviction → No
    if re.search(r"criminal|conviction", label_lower):
        return "No"

    # Background check → Yes
    if re.search(r"background\s*check", label_lower):
        return "Yes"

    # Disability → Prefer not to say / No
    if re.search(r"disabilit", label_lower):
        return "No"

    # Relocate → No (already in London)
    if re.search(r"relocat", label_lower):
        return "No"

    # Currently employed → Yes
    if re.search(r"currently\s*employ", label_lower):
        return "Yes"

    # 18+ / legal age → Yes
    if re.search(r"18\s*years|legal\s*age|over\s*18", label_lower):
        return "Yes"

    # Gender related → Female
    if re.search(r"gender|sex\b", label_lower):
        return "Female"

    return None


async def fill_text_field(page: Page, locator: Locator, label_text: str) -> bool:
    """Try to fill a text field based on its label."""
    answer = match_answer(label_text)
    if answer:
        await locator.fill("")
        await random_delay(0.2, 0.5)
        await locator.fill(answer)
        await random_delay(0.3, 0.6)
        return True
    return False


async def handle_radio_buttons(page: Page, fieldset: Locator, label_text: str) -> bool:
    """Handle yes/no or multiple choice radio buttons."""
    answer = match_yes_no(label_text)
    if not answer:
        answer = match_answer(label_text)
    if not answer:
        return False

    # Try to find the radio button matching our answer
    options = await fieldset.locator("label").all()
    for option in options:
        option_text = (await option.inner_text()).strip().lower()
        if answer.lower() in option_text or option_text in answer.lower():
            await option.click()
            await random_delay(0.2, 0.4)
            return True

    # Try partial match
    for option in options:
        option_text = (await option.inner_text()).strip().lower()
        if any(word in option_text for word in answer.lower().split()):
            await option.click()
            await random_delay(0.2, 0.4)
            return True

    return False


async def handle_dropdown(page: Page, select: Locator, label_text: str) -> bool:
    """Handle dropdown/select fields."""
    answer = match_answer(label_text)
    if not answer:
        return False

    # Get all options
    options = await select.locator("option").all()
    for option in options:
        option_text = (await option.inner_text()).strip().lower()
        if answer.lower() in option_text or option_text in answer.lower():
            value = await option.get_attribute("value")
            if value:
                await select.select_option(value=value)
            else:
                await select.select_option(label=await option.inner_text())
            await random_delay(0.2, 0.4)
            return True

    return False


async def upload_resume(page: Page, file_input: Locator) -> bool:
    """Upload resume to a file input."""
    if RESUME_PATH.exists():
        await file_input.set_input_files(str(RESUME_PATH))
        await random_delay(1, 2)
        return True
    return False


async def upload_cover_letter(page: Page, file_input: Locator, cover_letter_path: Path) -> bool:
    """Upload cover letter to a file input."""
    if cover_letter_path and cover_letter_path.exists():
        await file_input.set_input_files(str(cover_letter_path))
        await random_delay(1, 2)
        return True
    return False
