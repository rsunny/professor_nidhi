"""Generic Form Agent — handles any external application form not covered by platform-specific agents.

Catches: SmartRecruiters, iCIMS, Eightfold, Reed, custom company portals, etc.
Uses AI (haiku) to understand page layout and fill fields page-by-page.

Model: haiku
Max steps per page: 10
Called in a loop by orchestrator (like Workday agent).
"""

from __future__ import annotations

import os
from playwright.async_api import Page

from . import (
    AgentResult, FORM_TOOLS, run_agent, set_current_job,
    check_success_indicators, random_delay,
)


GENERIC_FORM_PROMPT = """You are filling a job application form on an external careers website.

RULES:
1. For EVERY empty field: call lookup_answer with the field label, then fill_field or select_option
2. Upload resume when you see a file upload input (file_type="resume")
3. Upload cover letter if there's a second file input (file_type="cover_letter")
4. After filling all visible fields, click Next/Continue/Submit to advance
5. Signal done(status="page_complete") after clicking Next (more pages may follow)
6. Signal done(status="submitted") if you see success/thank you text
7. Signal done(status="stuck") if you cannot proceed after 3 attempts

FIELD FILLING ORDER:
- Start from the top of the form, work down
- Fill text fields with fill_field
- For dropdowns: use select_option
- For radio buttons: click_element on the correct option label
- For checkboxes: click_element to check/uncheck

COMMON PATTERNS:
- "How did you hear about us?" → Always answer "LinkedIn"
- Resume/CV upload → upload_file with file_type="resume"
- Cover letter upload → upload_file with file_type="cover_letter"
- Agree to terms → click the checkbox
- "Already applied" text → done(status="submitted", reason="already applied")

IMPORTANT:
- Call lookup_answer for EVERY field before filling — never guess
- If lookup_answer returns "UNKNOWN", fill with the best reasonable answer
- Skip fields that already have a value
- If the page shows an error after clicking Submit, note it and signal stuck
- If the page redirects to a login page, signal done(status="stuck", reason="redirected to login")
"""


async def fill_generic_page(page: Page, job: dict, resume_path: str,
                             cover_letter_path: str = "") -> AgentResult:
    """Fill one page of a generic application form.

    Called in a loop by the orchestrator.
    """
    set_current_job(job)

    # Check for success before starting
    if await check_success_indicators(page):
        return AgentResult(success=True, status="submitted", data={"reason": "Success page detected"})

    # Check for already applied
    try:
        body = (await page.inner_text("body")).lower()
        if "already applied" in body or "previously applied" in body:
            return AgentResult(success=True, status="submitted", data={"reason": "Already applied"})
        if "thank you" in body and "application" in body:
            return AgentResult(success=True, status="submitted", data={"reason": "Thank you page"})
    except Exception:
        pass

    # Check for video/audio recording requirements — skip these
    if await _has_recording_requirement(page):
        return AgentResult(
            success=False, status="needs_recording",
            error="Video/audio recording required",
            data={"reason": "Job requires video or audio recording — skipped for manual review"}
        )

    result = await run_agent(
        page=page,
        system_prompt=GENERIC_FORM_PROMPT,
        tools=FORM_TOOLS,
        max_steps=10,
        model_tier="haiku",
        resume_path=resume_path,
        cover_letter_path=cover_letter_path,
        context_window=6,
    )

    return result


async def generic_form_loop(page: Page, job: dict, resume_path: str,
                             cover_letter_path: str = "", max_pages: int = 10) -> AgentResult:
    """Loop through pages of a generic form, calling fill_generic_page per page.

    Main entry point for generic form applications.
    """
    print(f"    [generic] Starting form fill...")

    for page_num in range(max_pages):
        print(f"    [generic] Page {page_num + 1}...")

        result = await fill_generic_page(page, job, resume_path, cover_letter_path)

        if result.status in ("submitted", "applied"):
            print(f"    [generic] Submitted on page {page_num + 1}!")
            return AgentResult(success=True, status="applied",
                               data={"pages_filled": page_num + 1, "reason": result.data.get("reason", "")})

        elif result.status in ("error", "stuck"):
            # Check if it's actually a success page we missed
            if await check_success_indicators(page):
                return AgentResult(success=True, status="applied",
                                   data={"pages_filled": page_num + 1, "reason": "Success detected after stuck"})
            print(f"    [generic] Stuck on page {page_num + 1}: {result.error}")
            return AgentResult(success=False, status="stuck",
                               error=f"Page {page_num + 1}: {result.error}",
                               data={"pages_filled": page_num})

        elif result.status == "max_steps":
            # Agent ran out of steps on this page — try to advance anyway
            from .navigation_agent import click_next_button
            nav_result = await click_next_button(page)
            if nav_result.success:
                await random_delay(2, 3)
                # Check if we landed on success
                if await check_success_indicators(page):
                    return AgentResult(success=True, status="applied",
                                       data={"pages_filled": page_num + 1})
                continue
            else:
                return AgentResult(success=False, status="max_steps",
                                   error=f"Stuck after {page_num + 1} pages",
                                   data={"pages_filled": page_num + 1})

        elif result.status == "page_complete":
            # Wait for next page to load
            await random_delay(2, 4)

            # Verify we actually moved forward (not stuck on same page)
            if await check_success_indicators(page):
                return AgentResult(success=True, status="applied",
                                   data={"pages_filled": page_num + 1})
            continue

        elif result.status in ("expired", "skipped"):
            return AgentResult(success=False, status=result.status,
                               data={"reason": result.data.get("reason", "")})

    # Exhausted pages
    return AgentResult(success=False, status="max_pages",
                       error=f"Filled {max_pages} pages without submission")


# ---------------------------------------------------------------------------
# Video/audio recording detection
# ---------------------------------------------------------------------------

async def _has_recording_requirement(page: Page) -> bool:
    """Detect if the current page requires video or audio recording."""
    try:
        # Check page content for recording indicators
        body = (await page.inner_text("body")).lower()
        recording_phrases = [
            "record your answer",
            "video answer",
            "video response",
            "record a video",
            "record your response",
            "video interview",
            "one-way video",
            "audio recording",
            "record audio",
            "before submitting",  # common with "record your answer before submitting"
        ]
        # Need at least one recording phrase
        has_phrase = any(phrase in body for phrase in recording_phrases)
        if not has_phrase:
            return False

        # Confirm with DOM elements (video/recording UI elements)
        has_recording_ui = await page.evaluate("""() => {
            const html = document.documentElement.innerHTML.toLowerCase();
            return (
                html.includes('video-answer') ||
                html.includes('video-record') ||
                html.includes('recording-mask') ||
                html.includes('pre-recording') ||
                html.includes('mediarecorder') ||
                html.includes('getusermedia') ||
                document.querySelector('[data-controller*="video"]') !== null ||
                document.querySelector('[class*="video-record"]') !== null ||
                document.querySelector('[class*="recording"]') !== null
            );
        }""")

        return has_phrase or has_recording_ui

    except Exception:
        return False
