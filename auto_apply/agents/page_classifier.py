"""Page Classifier Agent — single API call to identify what we're looking at.

Input: Page URL + visible text + interactive elements summary
Output: Structured classification (page type, platform, capabilities)
Model: haiku (fast, 1 call, <1 second)
"""

from __future__ import annotations

import json
import os
from playwright.async_api import Page

from . import get_client, resolve_model, get_interactive_elements, get_page_text, AgentResult


# ---------------------------------------------------------------------------
# Classification result structure
# ---------------------------------------------------------------------------

class PageClassification:
    """What the classifier returns."""
    def __init__(self, data: dict):
        self.page_type = data.get("page_type", "unknown")
        # login, form, success, error, job_listing, expired, captcha, unknown
        self.platform = data.get("platform", "generic")
        # workday, greenhouse, lever, taleo, smartrecruiters, icims, reed, generic
        self.has_linkedin_oauth = data.get("has_linkedin_oauth", False)
        self.has_apply_button = data.get("has_apply_button", False)
        self.has_easy_apply = data.get("has_easy_apply", False)
        self.form_field_count = data.get("form_field_count", 0)
        self.is_multi_page = data.get("is_multi_page", False)
        self.confidence = data.get("confidence", "low")
        self.notes = data.get("notes", "")

    def __repr__(self):
        return (f"PageClassification(type={self.page_type}, platform={self.platform}, "
                f"oauth={self.has_linkedin_oauth}, apply={self.has_apply_button}, "
                f"easy_apply={self.has_easy_apply}, fields={self.form_field_count})")


# ---------------------------------------------------------------------------
# Fast heuristic pre-classification (no AI needed for obvious cases)
# ---------------------------------------------------------------------------

async def heuristic_classify(page: Page) -> PageClassification | None:
    """Try to classify without AI using URL patterns and DOM checks.
    Returns None if uncertain (needs AI).
    """
    url = page.url.lower()

    # Expired/removed job
    try:
        body = (await page.inner_text("body")).lower()
        if any(x in body for x in [
            "no longer accepting", "no longer available", "this job has expired",
            "position has been filled", "job has been removed", "this listing has closed",
            "application deadline has passed",
        ]):
            return PageClassification({"page_type": "expired", "platform": _detect_platform_from_url(url), "confidence": "high"})

        # Success page
        if any(x in body for x in [
            "thank you for applying", "application submitted", "application received",
            "successfully submitted", "we have received your application",
        ]):
            return PageClassification({"page_type": "success", "platform": _detect_platform_from_url(url), "confidence": "high"})
    except Exception:
        pass

    # LinkedIn-specific quick checks
    if "linkedin.com" in url:
        try:
            # Check for Easy Apply button
            easy_btn = await page.query_selector(
                'button[aria-label*="Easy Apply"], .jobs-apply-button:has-text("Easy Apply")'
            )
            if easy_btn and await easy_btn.is_visible():
                return PageClassification({
                    "page_type": "job_listing",
                    "platform": "linkedin",
                    "has_easy_apply": True,
                    "has_apply_button": True,
                    "confidence": "high",
                })

            # Check for external Apply button
            apply_btn = await page.query_selector('a[aria-label*="Apply"], button:has-text("Apply")')
            if apply_btn and await apply_btn.is_visible():
                text = (await apply_btn.inner_text()).lower()
                if "easy" not in text:
                    return PageClassification({
                        "page_type": "job_listing",
                        "platform": "linkedin",
                        "has_easy_apply": False,
                        "has_apply_button": True,
                        "confidence": "high",
                    })
        except Exception:
            pass

        # Login wall
        if any(x in url for x in ["/login", "/authwall", "/uas/login"]):
            return PageClassification({"page_type": "login", "platform": "linkedin", "confidence": "high"})

    # Obvious login pages (from URL)
    if any(x in url for x in ["/login", "/signin", "/sign-in", "/auth", "/sso"]):
        platform = _detect_platform_from_url(url)
        # Check for LinkedIn OAuth
        has_oauth = False
        try:
            oauth_btn = await page.query_selector(
                'button:has-text("LinkedIn"), a:has-text("LinkedIn"), '
                '[class*="linkedin"], a[href*="linkedin.com/oauth"]'
            )
            if oauth_btn and await oauth_btn.is_visible():
                has_oauth = True
        except Exception:
            pass

        return PageClassification({
            "page_type": "login",
            "platform": platform,
            "has_linkedin_oauth": has_oauth,
            "confidence": "high",
        })

    # Account creation pages
    if any(x in url for x in ["/register", "/signup", "/sign-up", "/create-account", "/createaccount"]):
        return PageClassification({
            "page_type": "login",
            "platform": _detect_platform_from_url(url),
            "confidence": "high",
            "notes": "account_creation_page",
        })

    return None  # Need AI for this one


def _detect_platform_from_url(url: str) -> str:
    """Detect platform from URL patterns."""
    url = url.lower()
    if "myworkdayjobs.com" in url or "workday.com" in url:
        return "workday"
    if "greenhouse.io" in url or "boards.greenhouse" in url:
        return "greenhouse"
    if "lever.co" in url:
        return "lever"
    if "taleo" in url:
        return "taleo"
    if "smartrecruiters" in url:
        return "smartrecruiters"
    if "icims" in url:
        return "icims"
    if "reed.co.uk" in url:
        return "reed"
    if "linkedin.com" in url:
        return "linkedin"
    if "successfactors" in url:
        return "successfactors"
    if "eightfold" in url:
        return "eightfold"
    return "generic"


# ---------------------------------------------------------------------------
# AI-powered classification (for ambiguous pages)
# ---------------------------------------------------------------------------

CLASSIFIER_PROMPT = """You are a page classifier for a job application automation system.
Analyze the page URL, visible text, and interactive elements to classify this page.

Respond with ONLY a JSON object (no other text):
{
  "page_type": "login|form|success|error|job_listing|expired|captcha|unknown",
  "platform": "workday|greenhouse|lever|taleo|smartrecruiters|icims|reed|eightfold|generic",
  "has_linkedin_oauth": true/false,
  "has_apply_button": true/false,
  "has_easy_apply": true/false,
  "form_field_count": <number of visible form fields>,
  "is_multi_page": true/false,
  "confidence": "high|medium|low",
  "notes": "brief explanation"
}

Classification rules:
- "login" = page requires authentication (has password field, sign-in form, or "create account" prompt)
- "form" = application form is visible and ready to fill (has input fields for name, email, resume, etc.)
- "success" = application was submitted successfully (thank you message, confirmation)
- "error" = something went wrong (error messages, 404, forbidden)
- "job_listing" = job description page with an Apply button (not yet on the form)
- "expired" = job no longer available
- "captcha" = CAPTCHA challenge blocking progress
- "unknown" = cannot determine

Platform detection:
- "workday" = URL contains myworkdayjobs.com or workday.com, or page has data-automation-id attributes
- "greenhouse" = URL contains greenhouse.io or boards.greenhouse
- "lever" = URL contains lever.co or jobs.lever.co
- "taleo" = URL contains taleo
- "smartrecruiters" = URL contains smartrecruiters.com
- "icims" = URL contains icims
- "reed" = URL contains reed.co.uk
- "eightfold" = URL contains eightfold.ai
- "generic" = anything else

has_linkedin_oauth: true if there's a "Sign in with LinkedIn" or "Apply with LinkedIn" button visible
has_apply_button: true if there's an Apply/Submit button visible (not yet on form)
has_easy_apply: true if LinkedIn Easy Apply button is visible
form_field_count: count visible input/select/textarea elements that appear to be form fields
is_multi_page: true if this looks like a multi-step wizard (progress bar, step indicators, "Next" button)
"""


async def classify_page(page: Page) -> PageClassification:
    """Classify the current page — uses heuristics first, then AI if needed.

    This is the main entry point. Always returns a PageClassification.
    """
    # Try fast heuristic first
    heuristic = await heuristic_classify(page)
    if heuristic and heuristic.confidence == "high":
        print(f"    [classify] Heuristic: {heuristic.page_type} ({heuristic.platform})")
        return heuristic

    # Need AI classification
    try:
        elements = await get_interactive_elements(page)
        page_text = await get_page_text(page, max_chars=2000)

        prompt_content = f"""URL: {page.url}

INTERACTIVE ELEMENTS (first 3000 chars):
{elements[:3000]}

VISIBLE TEXT (first 2000 chars):
{page_text}"""

        client = get_client()
        response = client.messages.create(
            model=resolve_model("haiku"),
            max_tokens=300,
            system=CLASSIFIER_PROMPT,
            messages=[{"role": "user", "content": prompt_content}],
        )

        text = response.content[0].text.strip()

        # Parse JSON from response
        # Handle case where model wraps in ```json
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        data = json.loads(text)
        result = PageClassification(data)
        print(f"    [classify] AI: {result.page_type} ({result.platform}) [{result.confidence}]")
        return result

    except Exception as e:
        # Fallback to heuristic result if we had one, otherwise unknown
        if heuristic:
            print(f"    [classify] AI failed, using heuristic: {heuristic.page_type}")
            return heuristic

        print(f"    [classify] Failed: {str(e)[:60]}, defaulting to unknown")
        return PageClassification({
            "page_type": "unknown",
            "platform": _detect_platform_from_url(page.url),
            "confidence": "low",
            "notes": f"Classification failed: {str(e)[:60]}",
        })
