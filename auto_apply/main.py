"""Main orchestrator — runs the job application automation."""

import asyncio
import json
import random
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from config import (
    parse_jobs_from_md,
    save_jobs_json,
    MAX_APPS_PER_HOUR,
    MIN_DELAY_SECONDS,
    MAX_DELAY_SECONDS,
    MODE,
    SCREENSHOTS_DIR,
    SCANNED_QUESTIONS_PATH,
)
from browser import create_browser_context, ensure_logged_in, save_session
from linkedin_apply import handle_easy_apply
from external_apply import handle_external_apply, detect_ats
from humanizer import (
    random_delay,
    simulate_reading,
    check_rate_limit,
    record_application,
    inter_application_delay,
)
from logger import log_application, get_applied_job_ids, print_summary, init_log


async def main():
    """Main entry point."""
    mode = MODE
    if len(sys.argv) > 1:
        mode = sys.argv[1]  # Allow override: python main.py scan

    print(f"\n{'='*60}")
    print(f"  NIDHI SHETTY — LinkedIn Job Application Bot")
    print(f"  Mode: {mode.upper()}")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # Parse jobs
    jobs = parse_jobs_from_md()
    save_jobs_json(jobs)
    print(f"[main] Loaded {len(jobs)} jobs from jobs_50.md")

    # Filter out already-applied jobs
    applied_ids = get_applied_job_ids()
    if applied_ids:
        print(f"[main] Skipping {len(applied_ids)} already-applied jobs")
        jobs = [j for j in jobs if j["id"] not in applied_ids]

    if not jobs:
        print("[main] No new jobs to process!")
        return

    # Initialize log
    init_log()

    # Launch browser
    async with async_playwright() as playwright:
        browser, context = await create_browser_context(playwright)
        page = await context.new_page()

        # Check if we're dealing with LinkedIn jobs or Reed jobs
        linkedin_jobs = [j for j in jobs if "linkedin.com" in j["url"]]
        reed_jobs = [j for j in jobs if "reed.co.uk" in j["url"]]

        # Login to LinkedIn if we have LinkedIn jobs
        if linkedin_jobs:
            logged_in = await ensure_logged_in(page)
            if not logged_in:
                print("[main] ERROR: Could not log in to LinkedIn.")
                print("[main] Please set LINKEDIN_PASSWORD in .env and restart.")
                await browser.close()
                return

        # Shuffle jobs to avoid sequential patterns (but keep priority order)
        # Sort by priority first, then shuffle within each priority group
        high_priority = [j for j in jobs if "HIGH" in j.get("priority", "") or "🟢" in j.get("priority", "")]
        med_priority = [j for j in jobs if "MEDIUM" in j.get("priority", "") or "🟡" in j.get("priority", "")]
        low_priority = [j for j in jobs if "LOWER" in j.get("priority", "") or "🟠" in j.get("priority", "")]

        random.shuffle(high_priority)
        random.shuffle(med_priority)
        random.shuffle(low_priority)

        ordered_jobs = high_priority + med_priority + low_priority
        print(f"[main] Processing order: {len(high_priority)} HIGH, {len(med_priority)} MEDIUM, {len(low_priority)} LOWER priority")

        # Track scanned questions for review
        all_scanned_questions = {}

        # Process each job
        for i, job in enumerate(ordered_jobs, 1):
            job_id = job["id"]
            company = job["company"]
            title = job["title"]

            print(f"\n[{i}/{len(ordered_jobs)}] #{job_id} — {title} @ {company}")
            print(f"  URL: {job['url'][:80]}...")

            # Rate limiting (only in apply mode)
            if mode == "apply":
                if not check_rate_limit(MAX_APPS_PER_HOUR):
                    print(f"  ⏸️  Rate limit reached. Waiting...")
                    await asyncio.sleep(3600 / MAX_APPS_PER_HOUR)

            try:
                # Determine application method
                if "linkedin.com" in job["url"]:
                    # Navigate and detect Easy Apply vs External
                    await page.goto(job["url"], wait_until="domcontentloaded")
                    await random_delay(2, 4)

                    # Simulate reading the job
                    if mode == "apply":
                        await simulate_reading(page, random.uniform(5, 15))

                    # Check for Easy Apply button
                    easy_apply_btn = await page.query_selector(
                        'button[class*="jobs-apply-button"]'
                    )
                    btn_text = ""
                    if easy_apply_btn:
                        btn_text = (await easy_apply_btn.inner_text()).strip().lower()

                    if easy_apply_btn and "easy apply" in btn_text:
                        print(f"  📋 Method: Easy Apply")
                        result = await handle_easy_apply(page, job, mode=mode)
                        method = "easy_apply"
                    else:
                        print(f"  🔗 Method: External Application")
                        result = await handle_external_apply(page, context, job, mode=mode)
                        method = "external"

                elif "reed.co.uk" in job["url"]:
                    print(f"  🔗 Method: Reed.co.uk")
                    new_page = await context.new_page()
                    await new_page.goto(job["url"], wait_until="domcontentloaded")
                    await random_delay(2, 4)

                    from external_apply import handle_reed
                    if mode == "scan":
                        from form_filler import scan_form_questions
                        questions = await scan_form_questions(new_page)
                        result = {"status": "scanned", "notes": f"Scanned Reed page", "questions": questions}
                    else:
                        result = await handle_reed(new_page, job)

                    await new_page.close()
                    method = "reed"
                else:
                    print(f"  ⚠️  Unknown job source — skipping")
                    result = {"status": "skipped", "notes": "Unknown job URL format"}
                    method = "unknown"

                # Log result
                status = result.get("status", "failed")
                notes = result.get("notes", "")
                log_application(job, method, status, notes)

                # Track questions from scan mode
                if mode == "scan" and result.get("questions"):
                    all_scanned_questions[f"#{job_id} - {company} - {title}"] = result["questions"]

                # Status emoji
                emoji = {"applied": "✅", "scanned": "🔍", "skipped": "⏭️", "expired": "⏳", "failed": "❌"}.get(status, "❓")
                print(f"  {emoji} Status: {status}")
                if notes:
                    print(f"     Notes: {notes}")

                # Screenshot on failure (apply mode only)
                if mode == "apply" and status == "failed":
                    screenshot_path = SCREENSHOTS_DIR / f"fail_{job_id}_{company.replace(' ', '_')}.png"
                    await page.screenshot(path=str(screenshot_path))
                    print(f"     📸 Screenshot saved: {screenshot_path.name}")

                # Record application for rate limiting
                if status == "applied":
                    record_application()

                # Inter-application delay (only in apply mode)
                if mode == "apply" and i < len(ordered_jobs):
                    await inter_application_delay(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
                elif mode == "scan":
                    await random_delay(3, 8)  # Lighter delay in scan mode

            except Exception as e:
                print(f"  ❌ Unexpected error: {str(e)[:200]}")
                log_application(job, "unknown", "failed", f"Exception: {str(e)[:200]}")

                # Screenshot on exception
                try:
                    screenshot_path = SCREENSHOTS_DIR / f"error_{job_id}_{company.replace(' ', '_')}.png"
                    await page.screenshot(path=str(screenshot_path))
                except Exception:
                    pass

        # Save scanned questions
        if mode == "scan" and all_scanned_questions:
            with open(SCANNED_QUESTIONS_PATH, "w") as f:
                json.dump(all_scanned_questions, f, indent=2)
            print(f"\n[main] Scanned questions saved to: {SCANNED_QUESTIONS_PATH}")
            print_scanned_summary(all_scanned_questions)

        # Save session state
        await save_session(context)

        # Print summary
        print_summary()

        # Close browser
        await browser.close()


def print_scanned_summary(questions: dict):
    """Print a summary of scanned questions for user review."""
    print(f"\n{'='*60}")
    print(f"  SCANNED QUESTIONS SUMMARY")
    print(f"{'='*60}")

    unknown_questions = set()
    for job_key, job_questions in questions.items():
        for q in job_questions:
            if not q.get("has_answer"):
                unknown_questions.add(q["label"])

    if unknown_questions:
        print(f"\n⚠️  QUESTIONS WITHOUT ANSWERS ({len(unknown_questions)}):")
        print("  These need manual answers before running in 'apply' mode:\n")
        for i, q in enumerate(sorted(unknown_questions), 1):
            print(f"  {i}. {q}")
        print(f"\n  → Add answers to data/application_answers.json")
        print(f"  → Then run: python main.py apply")
    else:
        print(f"\n✅ All detected questions have answers!")
        print(f"  → Ready to run: python main.py apply")


if __name__ == "__main__":
    asyncio.run(main())
