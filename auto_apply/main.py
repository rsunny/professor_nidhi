"""Main entry point — orchestrates the job application flow."""

import asyncio
import random
import sys

from playwright.async_api import async_playwright

from config import (
    load_jobs,
    MAX_APPS_PER_HOUR,
    MIN_DELAY_SECONDS,
    MAX_DELAY_SECONDS,
    MODE,
    JOBS_JSON,
    DATA_DIR,
    COVER_LETTER_DIR,
)
from browser import create_browser_context, ensure_logged_in
from linkedin_apply import handle_easy_apply
from external_apply import handle_external_apply
from cover_letter_manager import parse_cover_letters, get_cover_letter_pdf
from humanizer import (
    RateLimiter,
    inter_application_delay,
    simulate_reading,
    random_delay,
)
from logger import log_application, get_applied_urls, print_summary, ensure_log_exists


async def main():
    """Main application loop."""
    print("=" * 60)
    print("  LINKEDIN JOB APPLICATION AUTOMATION")
    print("  Mode:", MODE.upper())
    print("=" * 60)

    # Generate jobs.json if it doesn't exist
    if not JOBS_JSON.exists():
        print("\n📋 Generating jobs.json from jobs_50.md...")
        from config import parse_jobs_from_markdown
        jobs_md = COVER_LETTER_DIR / "jobs_50.md"
        if jobs_md.exists():
            jobs = parse_jobs_from_markdown(str(jobs_md))
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            import json
            with open(JOBS_JSON, "w") as f:
                json.dump(jobs, f, indent=2)
            print(f"  Generated {len(jobs)} jobs")
        else:
            print(f"  ❌ jobs_50.md not found at {jobs_md}")
            sys.exit(1)

    # Load data
    jobs = load_jobs()
    cover_letters = parse_cover_letters()
    already_applied = get_applied_urls()
    ensure_log_exists()

    print(f"\n📊 Jobs loaded: {len(jobs)}")
    print(f"📝 Cover letters available: {len(cover_letters)}")
    print(f"✅ Already applied: {len(already_applied)}")

    # Filter out already-applied jobs
    remaining_jobs = [j for j in jobs if j["url"] not in already_applied]
    print(f"🎯 Remaining to apply: {len(remaining_jobs)}")

    if not remaining_jobs:
        print("\n🎉 All jobs have been applied to!")
        print_summary()
        return

    # Shuffle to avoid sequential patterns (but keep priority ordering somewhat)
    # Sort by priority first (HIGH first), then shuffle within each priority group
    high = [j for j in remaining_jobs if "HIGH" in j.get("priority", "") or "🟢" in j.get("priority", "")]
    medium = [j for j in remaining_jobs if "MEDIUM" in j.get("priority", "") or "🟡" in j.get("priority", "")]
    lower = [j for j in remaining_jobs if j not in high and j not in medium]
    random.shuffle(high)
    random.shuffle(medium)
    random.shuffle(lower)
    ordered_jobs = high + medium + lower

    print(f"\n  Priority order: {len(high)} HIGH, {len(medium)} MEDIUM, {len(lower)} LOWER")
    print(f"\n{'='*60}")

    # Initialize rate limiter
    rate_limiter = RateLimiter(max_per_hour=MAX_APPS_PER_HOUR)

    async with async_playwright() as playwright:
        browser, context = await create_browser_context(playwright)
        page = await ensure_logged_in(context)

        applied_count = 0
        failed_count = 0

        for idx, job in enumerate(ordered_jobs):
            job_id = job["id"]
            company = job.get("company", "Unknown")
            title = job.get("title", "Unknown")
            url = job["url"]

            print(f"\n{'─'*60}")
            print(f"  [{idx+1}/{len(ordered_jobs)}] Job #{job_id}: {title}")
            print(f"  Company: {company}")
            print(f"  URL: {url[:80]}...")

            # Rate limiting
            if not rate_limiter.can_apply():
                wait = rate_limiter.wait_time()
                print(f"  ⏳ Rate limit reached — waiting {wait:.0f}s")
                await asyncio.sleep(wait)

            # Get cover letter for this job
            cover_letter_path = get_cover_letter_pdf(job, cover_letters)
            if cover_letter_path:
                print(f"  📄 Cover letter: {cover_letter_path.name}")
            else:
                print(f"  📄 Cover letter: generic")

            # Determine application method
            is_linkedin = "linkedin.com" in url
            result = "failed"

            if is_linkedin:
                # Try Easy Apply first
                result = await handle_easy_apply(page, job, cover_letter_path)

                # If it returned "external", handle as external
                if result == "external":
                    print(f"  🔗 Redirecting to external application...")
                    result = await handle_external_apply(page, job, cover_letter_path)
            else:
                # Direct external URL (Reed, etc.)
                result = await handle_external_apply(page, job, cover_letter_path)

            # Log result
            method = "easy_apply" if is_linkedin and result != "external" else "external"
            log_application(
                job_id=job_id,
                company=company,
                title=title,
                url=url,
                method=method,
                status=result,
            )

            # Track counts
            if result == "applied":
                applied_count += 1
                rate_limiter.record_application()
                print(f"  ✅ SUCCESS — Total applied: {applied_count}")
            elif result == "failed":
                failed_count += 1
                print(f"  ❌ FAILED — Total failed: {failed_count}")
            elif result == "expired":
                print(f"  ⏰ EXPIRED")
            elif result == "skipped":
                print(f"  ⏭️  SKIPPED")

            # Inter-application delay (human-like)
            if idx < len(ordered_jobs) - 1:
                await inter_application_delay(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)

        # Save session state
        from config import STORAGE_STATE, OUTPUT_DIR
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(STORAGE_STATE))

        # Cleanup
        await browser.close()

    # Print final summary
    print_summary()


if __name__ == "__main__":
    asyncio.run(main())
