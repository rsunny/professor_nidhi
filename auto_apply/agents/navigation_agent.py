"""Navigation Agent — finds and clicks specific buttons on a page.

Dead simple: "Find the Apply button and click it." Nothing else.
Uses heuristics first (fast), falls back to AI only if needed.

Model: haiku (only if heuristics fail)
Max steps: 3
"""

from __future__ import annotations

import json
import os
import re
from playwright.async_api import Page, BrowserContext

from . import (
    AgentResult, get_client, resolve_model, get_interactive_elements,
    click_element_by_index, random_delay,
)


# ---------------------------------------------------------------------------
# Heuristic button finding (no AI, instant)
# ---------------------------------------------------------------------------

async def click_apply_button(page: Page, context: BrowserContext) -> AgentResult:
    """Find and click an Apply button. Returns info about what happened.

    Tries:
    1. Heuristic selectors (instant)
    2. AI-powered search (if heuristics fail)
    """
    # Try heuristic selectors first
    result = await _heuristic_click_apply(page, context)
    if result.success:
        return result

    # Fall back to AI
    return await _ai_click_button(page, context, target="Apply button",
                                   description="Click the Apply, Apply Now, or Submit Application button")


async def click_next_button(page: Page) -> AgentResult:
    """Find and click Next/Continue/Submit button."""
    selectors = [
        'button:has-text("Next")', 'button:has-text("Continue")',
        'button:has-text("Submit")', 'button:has-text("Save and Continue")',
        'button:has-text("Save & Continue")', 'button:has-text("Proceed")',
        'button[type="submit"]', 'input[type="submit"]',
        'button[data-automation-id="bottom-navigation-next-button"]',  # Workday
        'button:has-text("Review")', 'button:has-text("Send Application")',
    ]

    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                text = (await btn.inner_text()).strip()
                await btn.click()
                await random_delay(2, 4)
                return AgentResult(success=True, status="clicked",
                                   data={"button_text": text, "selector": sel})
        except Exception:
            continue

    return AgentResult(success=False, status="no_button", error="No Next/Continue/Submit button found")


async def click_submit_button(page: Page) -> AgentResult:
    """Find and click the final Submit/Apply button."""
    selectors = [
        'button:has-text("Submit Application")', 'button:has-text("Submit application")',
        'button:has-text("Submit")', 'button:has-text("Apply")',
        'button:has-text("Send Application")', 'button:has-text("Confirm")',
        'button:has-text("Complete Application")',
        'button[aria-label*="Submit"]', 'button[aria-label*="submit"]',
        'input[type="submit"][value*="Submit"]',
        'input[type="submit"][value*="Apply"]',
    ]

    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                text = (await btn.inner_text()).strip()
                await btn.click()
                await random_delay(3, 5)
                return AgentResult(success=True, status="submitted",
                                   data={"button_text": text})
        except Exception:
            continue

    return AgentResult(success=False, status="no_submit", error="No Submit button found")


# ---------------------------------------------------------------------------
# Heuristic Apply button click
# ---------------------------------------------------------------------------

async def _heuristic_click_apply(page: Page, context: BrowserContext) -> AgentResult:
    """Try clicking Apply button using common selectors."""
    # Platform-specific Apply buttons
    apply_selectors = [
        # Workday
        '[data-automation-id="jobPostingApplyButton"]',
        'a[data-automation-id="jobPostingApplyButton"]',
        # Generic
        'a[aria-label*="Apply"]',
        'button[aria-label*="Apply"]',
        'a:has-text("Apply Now")',
        'button:has-text("Apply Now")',
        'a:has-text("Apply for this job")',
        'button:has-text("Apply for this job")',
        'a:has-text("Apply")',
        'button:has-text("Apply")',
        # Greenhouse
        'a:has-text("Apply for this Job")',
        '#apply_button',
        'a[href*="/apply"]',
        # Lever
        'a.postings-btn',
        'a:has-text("Apply for this job")',
        # Reed
        'button:has-text("Apply")',
        'a.apply-button',
    ]

    for sel in apply_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                text = (await btn.inner_text()).strip().lower()

                # Skip "Easy Apply" buttons (handled separately)
                if "easy" in text:
                    continue
                # Skip filter pills/chips
                classes = (await btn.get_attribute("class")) or ""
                if "filter" in classes or "pill" in classes or "chip" in classes:
                    continue
                # Skip if text is too long (probably not a button)
                if len(text) > 50:
                    continue

                print(f"    [nav] Clicking: '{text[:30]}' ({sel[:40]})")

                # Track pages before click (Apply might open new tab)
                pages_before = context.pages[:]
                await btn.click()
                await random_delay(3, 5)

                # Check for new tab
                new_pages = [p for p in context.pages if p not in pages_before]
                if new_pages:
                    new_page = new_pages[-1]
                    try:
                        await new_page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                    return AgentResult(
                        success=True, status="clicked",
                        data={"button_text": text, "new_tab": True, "new_page_url": new_page.url}
                    )

                return AgentResult(
                    success=True, status="clicked",
                    data={"button_text": text, "new_tab": False}
                )
        except Exception:
            continue

    return AgentResult(success=False, status="no_button", error="No Apply button found via heuristics")


# ---------------------------------------------------------------------------
# AI-powered button finding (fallback)
# ---------------------------------------------------------------------------

async def _ai_click_button(page: Page, context: BrowserContext, target: str, description: str) -> AgentResult:
    """Use AI to find and click a button when heuristics fail."""
    try:
        elements = await get_interactive_elements(page)
        if not elements.strip():
            return AgentResult(success=False, status="empty_page", error="No interactive elements")

        client = get_client()
        prompt = f"""Find the {target} on this page and tell me its index number.

Page URL: {page.url}

Elements:
{elements[:4000]}

{description}

Respond with ONLY a JSON object:
{{"index": <number>, "description": "what the button says"}}

If no matching button exists, respond:
{{"index": -1, "description": "not found"}}
"""

        response = client.messages.create(
            model=resolve_model("haiku"),
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        # Parse JSON
        if text.startswith("{"):
            data = json.loads(text)
        else:
            match = re.search(r'\{[^{}]+\}', text)
            if match:
                data = json.loads(match.group())
            else:
                return AgentResult(success=False, status="parse_error", error="Could not parse AI response")

        idx = data.get("index", -1)
        if idx < 0:
            return AgentResult(success=False, status="not_found",
                               error=f"AI says: {data.get('description', 'no button')}")

        # Click the element
        pages_before = context.pages[:]
        success = await click_element_by_index(page, idx)
        if not success:
            return AgentResult(success=False, status="click_failed",
                               error="Element could not be clicked")

        await random_delay(3, 5)

        # Check for new tab
        new_pages = [p for p in context.pages if p not in pages_before]
        return AgentResult(
            success=True, status="clicked",
            data={
                "button_text": data.get("description", ""),
                "new_tab": bool(new_pages),
                "new_page_url": new_pages[-1].url if new_pages else None,
            }
        )

    except Exception as e:
        return AgentResult(success=False, status="ai_error", error=str(e)[:100])
