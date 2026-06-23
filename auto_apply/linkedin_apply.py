"""LinkedIn Easy Apply handler — uses AI agent with tool_use to fill forms."""

import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from config import RESUME_PATH, SCREENSHOTS_DIR, MODE, load_answers
from humanizer import random_delay, simulate_reading, random_mouse_move
from ai_navigator import (
    get_client, get_interactive_elements, format_accessibility_tree,
    click_element_by_index, fill_element_by_index, select_element_by_index,
    upload_file_by_index, parse_action, dismiss_overlays,
)
from profile_tools import (
    FORM_TOOLS, execute_lookup, build_tool_system_prompt, build_tool_submit_prompt,
    set_current_job, get_cover_letter_for_job,
)


async def handle_easy_apply(page: Page, job: dict, cover_letter_path: Path = None) -> str:
    """
    Handle LinkedIn Easy Apply for a job using AI navigation.

    1. Clicks Easy Apply to open the form
    2. AI agent fills all form fields intelligently
    3. AI uploads resume + cover letter
    4. Stops before Submit (user verifies & clicks Submit)

    Returns: "applied", "failed", "skipped", "expired", "external"
    """
    job_url = job["url"]
    job_id = job.get("id", 0)
    company = job.get("company", "Unknown")

    try:
        # Navigate to job page
        await page.goto(job_url, wait_until="domcontentloaded")
        await random_delay(8, 15)  # Human-like pause

        # Check if LinkedIn redirected to search (job expired/removed)
        current_url = page.url
        if "/jobs/search" in current_url or "/jobs/collections" in current_url:
            if "/jobs/view/" not in current_url:
                print(f"  ⏰ Job #{job_id} at {company} — redirected to search (likely expired)")
                return "expired"

        # Simulate reading the job description
        await simulate_reading(page, duration_sec=10)
        await random_mouse_move(page)
        await random_delay(3, 6)

        # Check if job is still available
        try:
            page_text = await page.inner_text("body")
            if "no longer accepting" in page_text.lower() or "job is closed" in page_text.lower():
                print(f"  ⏰ Job #{job_id} at {company} is expired/closed")
                return "expired"
        except Exception:
            pass

        # Check if this is Easy Apply or external
        # Use specific selectors that target the job detail panel's Easy Apply button,
        # NOT the search filter chip in the top bar
        easy_apply_el = page.locator(
            '.jobs-apply-button:has-text("Easy Apply"), '
            'button.jobs-apply-button--top-card:has-text("Easy Apply"), '
            '.job-details-jobs-unified-top-card__container button:has-text("Easy Apply"), '
            '.jobs-unified-top-card button:has-text("Easy Apply"), '
            '.jobs-details__main-content button:has-text("Easy Apply"), '
            'button[aria-label*="Easy Apply"][aria-label*="to"]'
        ).first

        is_easy_apply = False
        try:
            is_easy_apply = await easy_apply_el.is_visible(timeout=8000)
        except Exception:
            pass

        # Fallback: broader check but verify it's not the search filter
        if not is_easy_apply:
            broad_el = page.locator(
                'button:has-text("Easy Apply"), a:has-text("Easy Apply")'
            ).first
            try:
                if await broad_el.is_visible(timeout=3000):
                    # Verify it's a real Easy Apply button (not a filter chip)
                    # by checking its classes and parent context
                    classes = await broad_el.get_attribute("class") or ""
                    aria_label = await broad_el.get_attribute("aria-label") or ""
                    # Filter chips have specific classes like "search-reusables__filter-pill-button"
                    if "filter" not in classes and "pill" not in classes:
                        # Additional check: real Easy Apply buttons have aria-labels like
                        # "Easy Apply to Job Title at Company"
                        if "easy apply to" in aria_label.lower() or "jobs-apply-button" in classes:
                            easy_apply_el = broad_el
                            is_easy_apply = True
            except Exception:
                pass

        if not is_easy_apply:
            # Check for external "Apply" button
            apply_el = page.locator(
                'button:has-text("Apply"), a:has-text("Apply")'
            ).first
            try:
                if await apply_el.is_visible(timeout=3000):
                    el_text = await apply_el.inner_text()
                    if "easy apply" not in el_text.lower():
                        print(f"  🔗 Job #{job_id} has 'Apply' (external) — skipping for Step 4")
                        return "external"
            except Exception:
                pass
            print(f"  ⚠️  No Easy Apply button found for job #{job_id}")
            return "skipped"

        # Click Easy Apply to open the form
        print(f"  📋 Clicking Easy Apply...")
        await random_delay(2, 4)
        await easy_apply_el.click()
        await random_delay(8, 12)  # Wait for modal/form to load

        # Wait for the Easy Apply modal to appear
        modal_selector = (
            '.jobs-easy-apply-modal, '
            '[role="dialog"][aria-labelledby*="easy-apply"], '
            '.artdeco-modal:has(form), '
            '[data-test-modal], '
            '.jobs-easy-apply-content'
        )
        try:
            await page.wait_for_selector(modal_selector, timeout=10000)
            print(f"  ✅ Easy Apply modal detected")
        except Exception:
            # Modal not found — try clicking again
            print(f"  ⚠️  Modal not detected, retrying click...")
            await easy_apply_el.click()
            await random_delay(5, 8)
            try:
                await page.wait_for_selector(modal_selector, timeout=8000)
            except Exception:
                print(f"  ❌ Easy Apply modal failed to open")
                return "failed"

        # --- AI AGENT FILLS THE FORM ---
        print(f"  🤖 AI agent filling application form...")
        result = await ai_fill_easy_apply(page, job, cover_letter_path)

        if result == "scanned":
            if MODE == "auto":
                # Auto-submit: click the Submit button
                print(f"  🚀 Auto-submitting application for job #{job_id}...")
                submit_btn = page.locator(
                    'button:has-text("Submit application"), '
                    'button:has-text("Submit"), '
                    'button[aria-label*="Submit"]'
                ).first
                try:
                    if await submit_btn.is_visible(timeout=5000):
                        await random_delay(2, 4)
                        await submit_btn.click()
                        await random_delay(5, 8)
                        print(f"  ✅ Auto-submitted job #{job_id}")
                        return "applied"
                    else:
                        print(f"  ⚠️  Submit button not found — marking as scanned")
                        await take_screenshot(page, job_id, "no_submit_btn")
                        return "failed"
                except Exception as e:
                    print(f"  ⚠️  Auto-submit error: {e}")
                    await take_screenshot(page, job_id, "submit_error")
                    return "failed"
            else:
                # Review mode: pause for user to verify
                print(f"\n  ╔══════════════════════════════════════════════════════╗")
                print(f"  ║  FORM FILLED — Ready for submission                  ║")
                print(f"  ║  Job #{job_id}: {company[:40]:<40} ║")
                print(f"  ╠══════════════════════════════════════════════════════╣")
                print(f"  ║  👉 Please VERIFY the form in the browser            ║")
                print(f"  ║  👉 Click 'Submit application' yourself when ready   ║")
                print(f"  ║  👉 Then come back here and press Enter              ║")
                print(f"  ║                                                      ║")
                print(f"  ║  Or type 'skip' to skip this job                     ║")
                print(f"  ╚══════════════════════════════════════════════════════╝")

                await take_screenshot(page, job_id, "ready_to_submit")

                # Wait for user input
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, lambda: input("      > "))

                if response.strip().lower() == "skip":
                    close_btn = page.locator('button[aria-label="Dismiss"], button[aria-label="Close"]').first
                    try:
                        if await close_btn.is_visible(timeout=2000):
                            await close_btn.click()
                    except Exception:
                        pass
                    return "skipped"
                return "applied"

        elif result == "applied":
            return "applied"
        elif result == "expired":
            return "expired"
        elif result == "external":
            return "external"
        else:
            await take_screenshot(page, job_id, "failed")
            return "failed"

    except PlaywrightTimeout:
        print(f"  ❌ Timeout on job #{job_id} at {company}")
        await take_screenshot(page, job_id, "timeout")
        return "failed"
    except Exception as e:
        print(f"  ❌ Error on job #{job_id}: {e}")
        await take_screenshot(page, job_id, "error")
        return "failed"


async def get_dialog_elements(page: Page) -> str:
    """Get interactive elements from the topmost visible dialog/modal.
    Falls back to full page if no dialog is found."""

    # Try to find a visible dialog using Playwright locator
    dialog = page.locator('[role="dialog"]:visible, .artdeco-modal:visible').first
    try:
        dialog_handle = await dialog.element_handle(timeout=3000)
    except Exception:
        dialog_handle = None

    if dialog_handle:
        # Scope to dialog element
        elements = await dialog_handle.evaluate("""(root) => {
            const results = [];
            // Clear old indices from entire document
            document.querySelectorAll('[data-ai-idx]').forEach(el => el.removeAttribute('data-ai-idx'));

            const els = root.querySelectorAll(
                'button, a, input, textarea, select, [role="button"], [role="link"], ' +
                '[role="checkbox"], [role="radio"], [role="combobox"], [role="option"], ' +
                '[role="listbox"], [tabindex="0"], label, [role="switch"]'
            );

            let idx = 0;
            for (const el of els) {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;

                const tag = el.tagName.toLowerCase();
                const type = el.getAttribute('type') || '';
                const text = (el.innerText || el.textContent || '').trim().substring(0, 80);
                const name = el.getAttribute('name') || '';
                const id = el.getAttribute('id') || '';
                const ariaLabel = el.getAttribute('aria-label') || '';
                const placeholder = el.getAttribute('placeholder') || '';
                const value = el.value || '';
                const role = el.getAttribute('role') || '';
                const checked = el.checked ? ' checked' : '';

                let desc = `[${idx}] <${tag}`;
                if (type) desc += ` type="${type}"`;
                if (id) desc += ` id="${id}"`;
                if (name) desc += ` name="${name}"`;
                if (role) desc += ` role="${role}"`;
                desc += '>';

                if (ariaLabel) desc += ` aria-label="${ariaLabel}"`;
                if (placeholder) desc += ` placeholder="${placeholder}"`;
                if (text && text.length < 60) desc += ` text="${text}"`;
                if (value && (tag === 'input' || tag === 'select')) desc += ` value="${value}"`;
                if (checked) desc += checked;

                results.push(desc);
                el.setAttribute('data-ai-idx', idx.toString());
                idx++;
            }
            return results.join('\\n');
        }""")
        return elements
    else:
        # Fallback to full page
        return await get_interactive_elements(page)


async def ai_fill_easy_apply(page: Page, job: dict, cover_letter_path: Path = None) -> str:
    """
    AI agent fills LinkedIn Easy Apply forms using tool_use API.

    The form is already open (modal or page). AI reads the form,
    calls lookup_answer for each field, fills using structured tool calls,
    clicks Next, and repeats until it reaches Submit.
    Does NOT click Submit — returns "scanned" for user to verify.
    """
    client = get_client()

    resume_path = str(RESUME_PATH)
    cl_path = str(cover_letter_path) if cover_letter_path else ""

    # If no specific cover letter provided, resolve one for this job
    if not cl_path:
        resolved_cl = get_cover_letter_for_job(job)
        if resolved_cl:
            cl_path = resolved_cl

    # Set job context for motivation answer generation
    set_current_job(job)

    system_prompt = build_tool_system_prompt(job, resume_path, cl_path)

    messages = []
    max_steps = 40  # Enough for multi-page forms

    for step in range(max_steps):
        # Get current page state (scoped to dialog if one is visible)
        try:
            interactive = await get_dialog_elements(page)
        except Exception:
            await random_delay(3, 5)
            continue

        # Build the user message with page state
        user_msg = f"Step {step + 1}. Current form elements:\n\n{interactive}"

        messages.append({"role": "user", "content": user_msg})

        # Ask Claude what to do — with tools
        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1"),
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
            tools=FORM_TOOLS,
        )

        # Append assistant response
        messages.append({"role": "assistant", "content": response.content})

        # Process tool calls
        if response.stop_reason == "tool_use":
            tool_results = []
            done_status = None

            for block in response.content:
                if block.type != "tool_use":
                    continue

                result = await _execute_tool_call(page, block.name, block.input, resume_path, cl_path)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

                # Check if done was called
                if block.name == "done":
                    done_status = block.input.get("status", "scanned")
                    reason = block.input.get("reason", "")
                    print(f"    [ai] Done: {reason}")

            messages.append({"role": "user", "content": tool_results})

            if done_status:
                return done_status

        elif response.stop_reason == "end_turn":
            # AI stopped without tool call — extract any text for debugging
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    print(f"    [ai] Note: {block.text[:100]}")
            # Continue to next step — get fresh page state

    # Exceeded max steps
    print(f"  ⚠️  AI reached max steps ({max_steps})")
    return "failed"


async def _execute_tool_call(page: Page, tool_name: str, tool_input: dict,
                             resume_path: str, cover_letter_path: str) -> str:
    """Execute a single tool call and return the result string."""
    try:
        if tool_name == "lookup_answer":
            question = tool_input.get("question", "")
            field_type = tool_input.get("field_type", "text")
            options = tool_input.get("options")
            result = execute_lookup(question, field_type, options)
            print(f"    [tool] lookup_answer({question[:50]}...) -> {result[:80]}")
            return result

        elif tool_name == "fill_field":
            idx = tool_input["index"]
            value = tool_input["value"]
            display = value[:30] + '...' if len(value) > 30 else value
            print(f"    [tool] fill_field [{idx}]: '{display}'")
            success = await fill_element_by_index(page, idx, value)
            await random_delay(1, 3)
            return "filled successfully" if success else "ERROR: element not found at that index"

        elif tool_name == "select_option":
            idx = tool_input["index"]
            value = tool_input["value"]
            print(f"    [tool] select_option [{idx}]: '{value}'")
            success = await select_element_by_index(page, idx, value)
            await random_delay(1, 2)
            return "selected successfully" if success else "ERROR: element not found"

        elif tool_name == "click_element":
            idx = tool_input["index"]
            desc = tool_input.get("description", "")
            print(f"    [tool] click [{idx}]: {desc}")
            success = await click_element_by_index(page, idx)
            if success:
                await random_delay(3, 6)
                return "clicked successfully"
            return "ERROR: element not found or not clickable"

        elif tool_name == "upload_file":
            idx = tool_input["index"]
            file_type = tool_input.get("file_type", "resume")
            path = resume_path if file_type == "resume" else cover_letter_path
            print(f"    [tool] upload [{idx}]: {file_type}")
            if path:
                success = await upload_file_by_index(page, idx, path)
                await random_delay(3, 5)
                return "uploaded successfully" if success else "ERROR: upload failed"
            return "ERROR: file path not available"

        elif tool_name == "done":
            status = tool_input.get("status", "scanned")
            reason = tool_input.get("reason", "")
            return f"done: {status} - {reason}"

        else:
            return f"ERROR: unknown tool '{tool_name}'"

    except Exception as e:
        error_msg = str(e)[:150]
        print(f"    [tool] Error in {tool_name}: {error_msg}")
        return f"ERROR: {error_msg}"


def _build_form_fill_prompt(job: dict, answers: dict, resume_path: str, cover_letter_path: str) -> str:
    """Build system prompt for the AI form filler.

    DEPRECATED: This is kept for backwards compatibility with two_pass_apply.py's
    pass2 (ai_fill_and_submit). New code should use build_tool_system_prompt() from
    profile_tools.py instead.
    """
    return build_tool_system_prompt(job, resume_path, cover_letter_path)


async def take_screenshot(page: Page, job_id: int, suffix: str = "fail"):
    """Take a screenshot for debugging."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOTS_DIR / f"job_{job_id}_{suffix}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
    except Exception:
        pass
