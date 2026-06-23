"""Two-Pass Easy Apply — Extract Q&A first (dry run), then apply with verified answers.

Pass 1: Visit each job, open Easy Apply, AI fills form, capture Q&A, close without submitting.
Pass 2: Re-visit each job, fill with verified answers from qa_extract.json, submit.

Usage:
    python3 -u two_pass_apply.py --pass 1   # Extract Q&A (no submissions)
    python3 -u two_pass_apply.py --pass 2   # Apply with verified answers
"""

import argparse
import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

from config import STORAGE_STATE, DATA_DIR, OUTPUT_DIR, RESUME_PATH, load_answers
from browser import create_browser_context, ensure_logged_in
from ai_navigator import (
    get_client, click_element_by_index, fill_element_by_index,
    select_element_by_index, upload_file_by_index, parse_action,
)
from linkedin_apply import get_dialog_elements, _build_form_fill_prompt, _execute_tool_call
from profile_tools import (
    FORM_TOOLS, execute_lookup, build_tool_system_prompt, build_tool_submit_prompt,
    set_current_job, get_cover_letter_for_job,
)

# Target search URL
SEARCH_URL = (
    "https://www.linkedin.com/jobs/search/?"
    "keywords=Financial%20operations%20analyst"
    "&geoId=90009496"
    "&f_TPR=r2592000"
    "&f_AL=true"
)

QA_FILE = DATA_DIR / "qa_extract.json"
LOG_FILE = OUTPUT_DIR / "applications_log.csv"


# ---------------------------------------------------------------------------
# Pass 1: Extract Q&A (dry run)
# ---------------------------------------------------------------------------

async def pass1_extract_qa():
    """
    Navigate to search URL, collect job cards, for each job:
    - Click into job listing
    - Click Easy Apply
    - AI fills form, capturing Q&A at each step
    - Close modal without submitting
    - Save results to qa_extract.json
    """
    print("=" * 60)
    print("  PASS 1: EXTRACT Q&A (DRY RUN — NO SUBMISSIONS)")
    print("=" * 60, flush=True)

    async with async_playwright() as playwright:
        browser, context = await create_browser_context(playwright)
        page = await ensure_logged_in(context)

        # Navigate to search results
        print(f"\n  Navigating to search URL...")
        await page.goto(SEARCH_URL, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(5, 8))

        # Collect all job cards from search results (with pagination)
        jobs = await collect_all_jobs_from_search(page)
        print(f"\n  Found {len(jobs)} jobs in search results")

        if not jobs:
            print("  No jobs found. Exiting.")
            await browser.close()
            return

        # Load existing QA data to resume from where we left off
        existing_qa = []
        processed_urls = set()
        if QA_FILE.exists():
            with open(QA_FILE) as f:
                existing_qa = json.load(f)
            processed_urls = {item["job_url"] for item in existing_qa}
            print(f"  Loaded {len(existing_qa)} existing Q&A entries (will skip)")

        qa_results = list(existing_qa)

        for idx, job in enumerate(jobs):
            job_url = job["url"]
            if job_url in processed_urls:
                continue

            print(f"\n  [{idx+1}/{len(jobs)}] {job.get('title', 'Unknown')} at {job.get('company', 'Unknown')}")
            print(f"    URL: {job_url}")

            try:
                qa_entry = await extract_qa_for_job(page, job)
                qa_results.append(qa_entry)
                processed_urls.add(job_url)

                # Save progress after each job
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                with open(QA_FILE, "w") as f:
                    json.dump(qa_results, f, indent=2)

            except Exception as e:
                print(f"    ERROR: {str(e)[:100]}")
                qa_results.append({
                    "job_url": job_url,
                    "job_title": job.get("title", ""),
                    "company": job.get("company", ""),
                    "status": "error",
                    "error": str(e)[:200],
                    "questions_and_answers": [],
                })

            # Brief delay between jobs
            await asyncio.sleep(random.uniform(5, 10))

        # Final save
        with open(QA_FILE, "w") as f:
            json.dump(qa_results, f, indent=2)

        # Print summary
        print(f"\n{'=' * 60}")
        print(f"  PASS 1 COMPLETE")
        print(f"  Total jobs processed: {len(qa_results)}")
        successful = [r for r in qa_results if r.get("status") == "scanned"]
        print(f"  Successfully scanned: {len(successful)}")
        print(f"  Saved to: {QA_FILE}")
        print(f"{'=' * 60}")

        await context.storage_state(path=str(STORAGE_STATE))
        await browser.close()


async def collect_all_jobs_from_search(page: Page) -> list[dict]:
    """Collect all job cards from LinkedIn search results, with pagination."""
    all_jobs = []
    seen_urls = set()

    for page_num in range(1, 11):  # Up to 10 pages (250 jobs max)
        if page_num > 1:
            # Paginate
            page_url = SEARCH_URL + f"&start={(page_num - 1) * 25}"
            await page.goto(page_url, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(4, 7))

        # Scroll to load all cards
        await scroll_job_list(page)
        await asyncio.sleep(2)

        # Scrape job cards from current page
        page_jobs = await page.evaluate("""() => {
            const cards = document.querySelectorAll(
                '.job-card-container, ' +
                '.jobs-search-results__list-item, ' +
                'li[data-occludable-job-id], ' +
                '.scaffold-layout__list-item, ' +
                '.jobs-search-results-list__list-item'
            );
            const results = [];
            for (const card of cards) {
                const link = card.querySelector('a[href*="/jobs/view/"]');
                if (!link) continue;

                let href = link.href || link.getAttribute('href') || '';
                if (href.startsWith('/')) href = 'https://www.linkedin.com' + href;
                href = href.split('?')[0];

                const titleEl = card.querySelector(
                    '.job-card-list__title, ' +
                    '.artdeco-entity-lockup__title, ' +
                    'a[data-control-name="job_card_title"]'
                ) || link;
                const title = (titleEl.innerText || titleEl.textContent || '').trim().split('\\n')[0].trim();

                const companyEl = card.querySelector(
                    '.job-card-container__primary-description, ' +
                    '.artdeco-entity-lockup__subtitle, ' +
                    '.job-card-container__company-name, ' +
                    'span.job-card-list__company-name'
                );
                const company = companyEl ? companyEl.innerText.trim().split('\\n')[0] : '';

                if (href && href.includes('/jobs/view/') && title) {
                    results.push({url: href, title, company});
                }
            }
            return results;
        }""")

        new_count = 0
        for job in page_jobs:
            if job["url"] not in seen_urls:
                seen_urls.add(job["url"])
                all_jobs.append(job)
                new_count += 1

        print(f"    Page {page_num}: {len(page_jobs)} cards, {new_count} new (total: {len(all_jobs)})")

        # Stop if no new cards found
        if new_count == 0:
            break

        await asyncio.sleep(random.uniform(3, 5))

    return all_jobs


async def scroll_job_list(page: Page, max_scrolls=10):
    """Scroll the job list container to load lazy-loaded cards."""
    list_container = page.locator('.jobs-search-results-list, .scaffold-layout__list')
    for _ in range(max_scrolls):
        try:
            await list_container.evaluate("el => el.scrollTop = el.scrollTop + el.clientHeight")
        except Exception:
            await page.evaluate("window.scrollBy(0, 600)")
        await asyncio.sleep(0.8)
    # Scroll back to top
    try:
        await list_container.evaluate("el => el.scrollTop = 0")
    except Exception:
        pass


async def extract_qa_for_job(page: Page, job: dict) -> dict:
    """Open Easy Apply for a single job, fill with AI, capture Q&A, close without submitting."""
    job_url = job["url"]
    result = {
        "job_url": job_url,
        "job_title": job.get("title", ""),
        "company": job.get("company", ""),
        "status": "pending",
        "questions_and_answers": [],
    }

    # Navigate to job page
    await page.goto(job_url, wait_until="domcontentloaded")
    await asyncio.sleep(random.uniform(3, 5))

    # Check if redirected (expired)
    if "/jobs/search" in page.url and "/jobs/view/" not in page.url:
        result["status"] = "expired"
        return result

    # Find and click Easy Apply button
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

    if not is_easy_apply:
        # Fallback: broader check
        broad_el = page.locator('button:has-text("Easy Apply"), a:has-text("Easy Apply")').first
        try:
            if await broad_el.is_visible(timeout=3000):
                classes = await broad_el.get_attribute("class") or ""
                if "filter" not in classes and "pill" not in classes:
                    easy_apply_el = broad_el
                    is_easy_apply = True
        except Exception:
            pass

    if not is_easy_apply:
        result["status"] = "no_easy_apply"
        return result

    # Click Easy Apply
    print(f"    Clicking Easy Apply...")
    await easy_apply_el.click()
    await asyncio.sleep(random.uniform(3, 5))

    # Wait for modal
    modal_selector = (
        '.jobs-easy-apply-modal, '
        '[role="dialog"][aria-labelledby*="easy-apply"], '
        '.artdeco-modal:has(form), '
        '[data-test-modal], '
        '.jobs-easy-apply-content'
    )
    try:
        await page.wait_for_selector(modal_selector, timeout=10000)
    except Exception:
        # Retry click
        try:
            await easy_apply_el.click()
            await asyncio.sleep(random.uniform(3, 5))
            await page.wait_for_selector(modal_selector, timeout=8000)
        except Exception:
            result["status"] = "modal_failed"
            return result

    # AI fills the form and captures Q&A
    print(f"    AI filling form (capture mode)...")
    qa_list, status = await ai_fill_and_capture(page, job)
    result["questions_and_answers"] = qa_list
    result["status"] = status

    # Close modal without submitting
    await close_modal(page)

    return result


async def ai_fill_and_capture(page: Page, job: dict) -> tuple[list[dict], str]:
    """
    Run AI form filler using tool_use API, capturing all Q&A pairs.
    Returns (questions_and_answers_list, status).
    Does NOT submit — stops at Submit page.

    Q&A capture is automatic: every lookup_answer tool call is logged.
    """
    client = get_client()
    resume_path = str(RESUME_PATH)

    # Set job context for motivation answers and resolve cover letter
    set_current_job(job)
    cl_path = get_cover_letter_for_job(job) or ""

    system_prompt = build_tool_system_prompt(job, resume_path, cl_path)
    messages = []
    qa_captured = []
    max_steps = 40

    for step in range(max_steps):
        try:
            interactive = await get_dialog_elements(page)
        except Exception:
            await asyncio.sleep(2)
            continue

        user_msg = f"Step {step + 1}. Current form elements:\n\n{interactive}"
        messages.append({"role": "user", "content": user_msg})

        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1"),
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
            tools=FORM_TOOLS,
        )

        # Append assistant response
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            tool_results = []
            done_status = None

            for block in response.content:
                if block.type != "tool_use":
                    continue

                # Capture Q&A from lookup_answer calls
                if block.name == "lookup_answer":
                    question = block.input.get("question", "")
                    field_type = block.input.get("field_type", "text")
                    options = block.input.get("options")
                    result_str = execute_lookup(question, field_type, options)
                    result_data = json.loads(result_str)
                    qa_captured.append({
                        "action": "lookup_answer",
                        "question": question,
                        "field_type": field_type,
                        "options": options,
                        "answer": result_data.get("answer", ""),
                        "confidence": result_data.get("confidence", ""),
                        "source": result_data.get("source", ""),
                    })
                    print(f"      Q: {question[:60]} -> A: {result_data.get('answer', '')[:40]}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

                elif block.name == "fill_field":
                    idx = block.input.get("index")
                    value = block.input.get("value", "")
                    label = _extract_label_for_index(interactive, idx)
                    qa_captured.append({
                        "action": "FILL",
                        "index": idx,
                        "label": label,
                        "value": value,
                    })
                    result = await _execute_tool_call(page, block.name, block.input, resume_path, "")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

                elif block.name == "select_option":
                    idx = block.input.get("index")
                    value = block.input.get("value", "")
                    label = _extract_label_for_index(interactive, idx)
                    qa_captured.append({
                        "action": "SELECT",
                        "index": idx,
                        "label": label,
                        "value": value,
                    })
                    result = await _execute_tool_call(page, block.name, block.input, resume_path, "")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

                elif block.name == "upload_file":
                    idx = block.input.get("index")
                    file_type = block.input.get("file_type", "resume")
                    qa_captured.append({
                        "action": "UPLOAD",
                        "index": idx,
                        "label": f"Upload {file_type}",
                        "value": resume_path,
                    })
                    result = await _execute_tool_call(page, block.name, block.input, resume_path, "")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

                elif block.name == "done":
                    done_status = block.input.get("status", "scanned")
                    reason = block.input.get("reason", "")
                    print(f"      Done: {reason}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"done: {done_status} - {reason}",
                    })

                else:
                    # click_element or other tools
                    result = await _execute_tool_call(page, block.name, block.input, resume_path, "")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "user", "content": tool_results})

            if done_status:
                return qa_captured, done_status

        elif response.stop_reason == "end_turn":
            # AI stopped without tool call — continue to get fresh state
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    print(f"      Note: {block.text[:100]}")

    print(f"    Reached max steps ({max_steps})")
    return qa_captured, "max_steps"


def _extract_label_for_index(interactive: str, idx: int) -> str:
    """Extract the label/context for a given element index from the interactive elements text."""
    lines = interactive.split("\n")

    # Find the line for this index
    target_line = ""
    for line in lines:
        if line.strip().startswith(f"[{idx}]"):
            target_line = line
            break

    if not target_line:
        return f"element_{idx}"

    # Try to extract meaningful label from the element description
    # Check aria-label, placeholder, text, name, id
    import re
    for attr in ["aria-label", "placeholder", "text", "name", "id"]:
        match = re.search(rf'{attr}="([^"]+)"', target_line)
        if match:
            return match.group(1)

    # Look at preceding label element
    if idx > 0:
        for line in lines:
            if line.strip().startswith(f"[{idx - 1}]") and "<label" in line:
                match = re.search(r'text="([^"]+)"', line)
                if match:
                    return match.group(1)

    return target_line[:80]


async def close_modal(page: Page):
    """Close the Easy Apply modal without submitting."""
    # Try dismiss button
    dismiss_selectors = [
        'button[aria-label="Dismiss"]',
        'button[aria-label="Close"]',
        '.artdeco-modal__dismiss',
        'button[data-test-modal-close-btn]',
    ]
    for selector in dismiss_selectors:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await asyncio.sleep(1)
                # Handle "Discard" confirmation if it appears
                try:
                    discard_btn = page.locator(
                        'button:has-text("Discard"), button[data-test-dialog-primary-btn]'
                    ).first
                    if await discard_btn.is_visible(timeout=3000):
                        await discard_btn.click()
                        await asyncio.sleep(1)
                except Exception:
                    pass
                return
        except Exception:
            continue

    # Fallback: press Escape
    await page.keyboard.press("Escape")
    await asyncio.sleep(1)
    try:
        discard_btn = page.locator('button:has-text("Discard")').first
        if await discard_btn.is_visible(timeout=2000):
            await discard_btn.click()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Pass 2: Apply with verified answers
# ---------------------------------------------------------------------------

async def pass2_apply():
    """
    Load qa_extract.json, re-visit each job that was successfully scanned,
    fill with the SAME answers from Pass 1, and submit.
    """
    print("=" * 60)
    print("  PASS 2: APPLY WITH VERIFIED ANSWERS")
    print("=" * 60, flush=True)

    if not QA_FILE.exists():
        print(f"  ERROR: {QA_FILE} not found. Run --pass 1 first.")
        return

    with open(QA_FILE) as f:
        qa_data = json.load(f)

    # Filter to jobs that were successfully scanned
    to_apply = [entry for entry in qa_data if entry.get("status") == "scanned"]
    print(f"  Jobs to apply: {len(to_apply)} (out of {len(qa_data)} total)")

    if not to_apply:
        print("  No jobs with status 'scanned' found. Nothing to apply to.")
        return

    async with async_playwright() as playwright:
        browser, context = await create_browser_context(playwright)
        page = await ensure_logged_in(context)

        applied_count = 0
        failed_count = 0

        for idx, entry in enumerate(to_apply):
            job_url = entry["job_url"]
            job_title = entry.get("job_title", "Unknown")
            company = entry.get("company", "Unknown")
            qa_pairs = entry.get("questions_and_answers", [])

            print(f"\n  [{idx+1}/{len(to_apply)}] {job_title} at {company}")
            print(f"    URL: {job_url}")
            print(f"    Q&A pairs to replay: {len(qa_pairs)}")

            try:
                status = await apply_with_answers(page, entry)
                if status == "applied":
                    applied_count += 1
                    entry["status"] = "applied"
                    print(f"    APPLIED!")
                else:
                    failed_count += 1
                    entry["status"] = f"pass2_{status}"
                    print(f"    Result: {status}")

            except Exception as e:
                failed_count += 1
                entry["status"] = "pass2_error"
                entry["error"] = str(e)[:200]
                print(f"    ERROR: {str(e)[:100]}")

            # Save progress
            with open(QA_FILE, "w") as f:
                json.dump(qa_data, f, indent=2)

            # Log to CSV
            log_application(entry)

            # Delay between applications
            await asyncio.sleep(random.uniform(5, 10))

        print(f"\n{'=' * 60}")
        print(f"  PASS 2 COMPLETE")
        print(f"  Applied: {applied_count}")
        print(f"  Failed: {failed_count}")
        print(f"{'=' * 60}")

        await context.storage_state(path=str(STORAGE_STATE))
        await browser.close()


async def apply_with_answers(page: Page, entry: dict) -> str:
    """Navigate to job, open Easy Apply, fill using saved answers, and submit."""
    job_url = entry["job_url"]
    job = {"url": job_url, "title": entry.get("job_title", ""), "company": entry.get("company", "")}

    # Navigate to job page
    await page.goto(job_url, wait_until="domcontentloaded")
    await asyncio.sleep(random.uniform(3, 5))

    # Check if redirected (expired)
    if "/jobs/search" in page.url and "/jobs/view/" not in page.url:
        return "expired"

    # Find and click Easy Apply button
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

    if not is_easy_apply:
        broad_el = page.locator('button:has-text("Easy Apply"), a:has-text("Easy Apply")').first
        try:
            if await broad_el.is_visible(timeout=3000):
                classes = await broad_el.get_attribute("class") or ""
                if "filter" not in classes and "pill" not in classes:
                    easy_apply_el = broad_el
                    is_easy_apply = True
        except Exception:
            pass

    if not is_easy_apply:
        return "no_easy_apply"

    # Click Easy Apply
    await easy_apply_el.click()
    await asyncio.sleep(random.uniform(3, 5))

    # Wait for modal
    modal_selector = (
        '.jobs-easy-apply-modal, '
        '[role="dialog"][aria-labelledby*="easy-apply"], '
        '.artdeco-modal:has(form), '
        '[data-test-modal], '
        '.jobs-easy-apply-content'
    )
    try:
        await page.wait_for_selector(modal_selector, timeout=10000)
    except Exception:
        try:
            await easy_apply_el.click()
            await asyncio.sleep(random.uniform(3, 5))
            await page.wait_for_selector(modal_selector, timeout=8000)
        except Exception:
            return "modal_failed"

    # Use AI to fill and SUBMIT this time
    print(f"    AI filling and submitting...")
    status = await ai_fill_and_submit(page, job)
    return status


async def ai_fill_and_submit(page: Page, job: dict) -> str:
    """
    AI agent fills the form and clicks Submit using tool_use API.
    Same as ai_fill_easy_apply but instructs submission.
    """
    client = get_client()
    resume_path = str(RESUME_PATH)

    # Set job context for motivation answers and resolve cover letter
    set_current_job(job)
    cl_path = get_cover_letter_for_job(job) or ""

    # Use the submit variant of the system prompt
    system_prompt = build_tool_submit_prompt(job, resume_path, cl_path)

    messages = []
    max_steps = 40

    for step in range(max_steps):
        try:
            interactive = await get_dialog_elements(page)
        except Exception:
            await asyncio.sleep(2)
            continue

        user_msg = f"Step {step + 1}. Current form elements:\n\n{interactive}"
        messages.append({"role": "user", "content": user_msg})

        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1"),
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
            tools=FORM_TOOLS,
        )

        # Append assistant response
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            tool_results = []
            done_status = None

            for block in response.content:
                if block.type != "tool_use":
                    continue

                result = await _execute_tool_call(page, block.name, block.input, resume_path, "")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

                if block.name == "done":
                    done_status = block.input.get("status", "applied")
                    reason = block.input.get("reason", "")
                    print(f"      Done: {reason}")

            messages.append({"role": "user", "content": tool_results})

            if done_status:
                return done_status

        elif response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    print(f"      Note: {block.text[:100]}")

    return "max_steps"


def log_application(entry: dict):
    """Log application result to CSV."""
    import csv

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = LOG_FILE.exists()

    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "job_title", "company", "url", "status", "questions"])
        writer.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S"),
            entry.get("job_title", ""),
            entry.get("company", ""),
            entry.get("job_url", ""),
            entry.get("status", ""),
            len(entry.get("questions_and_answers", [])),
        ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Two-Pass Easy Apply")
    parser.add_argument("--pass", dest="pass_num", type=int, required=True,
                        choices=[1, 2], help="Pass number: 1=extract Q&A, 2=apply")
    args = parser.parse_args()

    if args.pass_num == 1:
        asyncio.run(pass1_extract_qa())
    elif args.pass_num == 2:
        asyncio.run(pass2_apply())


if __name__ == "__main__":
    main()
