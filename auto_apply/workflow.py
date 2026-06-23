"""
WORKFLOW ORCHESTRATOR
=====================
Step 1: Fetch all relevant jobs (LinkedIn, Reed, career pages)
Step 2: Filter & save (relevance, salary, location, seniority)
Step 3: LinkedIn Easy Apply (fastest — do these first)
Step 4: External applications by domain (Workday, Greenhouse, Lever, Reed, etc.)

Usage:
    python3 workflow.py              # Run full pipeline
    python3 workflow.py --step 1     # Only fetch jobs
    python3 workflow.py --step 2     # Only filter
    python3 workflow.py --step 3     # Only Easy Apply
    python3 workflow.py --step 4     # Only external apps
    python3 workflow.py --from 3     # Start from step 3
"""

import argparse
import asyncio
import json
import random
import re
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

from config import (
    BASE_DIR, DATA_DIR, OUTPUT_DIR, STORAGE_STATE, RESUME_PATH,
    GENERIC_COVER_LETTER, LINKEDIN_EMAIL, LINKEDIN_PASSWORD,
    MAX_APPS_PER_HOUR, MIN_DELAY_SECONDS, MAX_DELAY_SECONDS, MODE,
)
from browser import create_browser_context, ensure_logged_in
from linkedin_apply import handle_easy_apply
from external_apply import handle_external_apply, detect_ats
from cover_letter_manager import parse_cover_letters, get_cover_letter_pdf
from humanizer import RateLimiter, inter_application_delay, random_delay
from logger import log_application, get_applied_urls, print_summary, ensure_log_exists


# ============================================================
# STEP 1: FETCH JOBS
# ============================================================

async def step1_fetch_jobs():
    """Fetch jobs from LinkedIn, Reed, and company career pages."""
    print("\n" + "=" * 60)
    print("  STEP 1: FETCHING JOBS")
    print("=" * 60)

    from job_scraper import main as scraper_main
    await scraper_main()


# ============================================================
# STEP 2: FILTER & CATEGORIZE
# ============================================================

def step2_filter_and_categorize():
    """Load pre-filtered jobs from jobs_scraped.json and categorize by apply method."""
    print("\n" + "=" * 60)
    print("  STEP 2: FILTERING & CATEGORIZING")
    print("=" * 60)

    # Load pre-filtered/scored jobs from job_scraper output
    scraped_path = DATA_DIR / "jobs_scraped.json"
    if not scraped_path.exists():
        print("  ❌ No jobs_scraped.json found. Run Step 1 first.")
        return {}

    with open(scraped_path) as f:
        filtered = json.load(f)

    print(f"\n  Loaded {len(filtered)} pre-filtered jobs from jobs_scraped.json")

    # --- CATEGORIZE by apply method ---
    already_applied = get_applied_urls()
    remaining = [j for j in filtered if j['url'] not in already_applied]
    print(f"  Already applied: {len(filtered) - len(remaining)}")
    print(f"  Remaining to apply: {len(remaining)}")

    # Split into categories
    linkedin_easy_apply = []
    external_by_domain = {}

    for job in remaining:
        url = job.get('url', '')
        if 'linkedin.com' in url:
            linkedin_easy_apply.append(job)
        elif 'reed.co.uk' in url:
            external_by_domain.setdefault('reed', []).append(job)
        else:
            # Categorize by ATS domain
            ats = detect_ats(url)
            external_by_domain.setdefault(ats, []).append(job)

    # Sort each category by relevance score
    linkedin_easy_apply.sort(key=lambda j: j.get('relevance_score', 0), reverse=True)
    for domain in external_by_domain:
        external_by_domain[domain].sort(key=lambda j: j.get('relevance_score', 0), reverse=True)

    # Save categorized output
    categorized = {
        'linkedin_easy_apply': linkedin_easy_apply,
        'external_by_domain': external_by_domain,
    }
    cat_path = DATA_DIR / "jobs_categorized.json"
    with open(cat_path, 'w') as f:
        json.dump(categorized, f, indent=2, default=str)

    print(f"\n  --- CATEGORIES ---")
    print(f"  LinkedIn Easy Apply: {len(linkedin_easy_apply)} jobs")
    for domain, jobs_list in sorted(external_by_domain.items(), key=lambda x: -len(x[1])):
        print(f"  External ({domain}): {len(jobs_list)} jobs")
    print(f"\n  Saved: {cat_path}")

    return categorized


# ============================================================
# STEP 3: LINKEDIN EASY APPLY
# ============================================================

async def step3_linkedin_easy_apply():
    """Apply to all LinkedIn Easy Apply jobs."""
    print("\n" + "=" * 60)
    print("  STEP 3: LINKEDIN EASY APPLY")
    print("=" * 60)

    # Load categorized jobs
    cat_path = DATA_DIR / "jobs_categorized.json"
    if not cat_path.exists():
        print("  No categorized jobs found. Running Step 2 first...")
        step2_filter_and_categorize()

    with open(cat_path) as f:
        categorized = json.load(f)

    jobs = categorized.get('linkedin_easy_apply', [])

    # Filter out already-applied
    already_applied = get_applied_urls()
    jobs = [j for j in jobs if j['url'] not in already_applied]

    if not jobs:
        print("\n  All LinkedIn Easy Apply jobs already done!")
        return

    print(f"\n  LinkedIn jobs to check for Easy Apply: {len(jobs)}")
    print(f"  (Jobs without Easy Apply will be skipped to Step 4)")
    print(f"  Mode: USER VERIFIES & CLICKS SUBMIT")
    print(f"  Rate limit: {MAX_APPS_PER_HOUR}/hour")

    # Load cover letters
    cover_letters = parse_cover_letters()
    ensure_log_exists()

    rate_limiter = RateLimiter(max_per_hour=MAX_APPS_PER_HOUR)

    async with async_playwright() as playwright:
        browser, context = await create_browser_context(playwright)
        page = await ensure_logged_in(context)

        applied = 0
        failed = 0

        for idx, job in enumerate(jobs):
            job_id = job.get('id', idx)
            company = job.get('company', 'Unknown')
            title = job.get('title', 'Unknown')
            url = job['url']

            print(f"\n{'─' * 60}")
            print(f"  [{idx+1}/{len(jobs)}] #{job_id}: {title[:55]}")
            print(f"  Company: {company}")

            # Rate limiting
            if not rate_limiter.can_apply():
                wait = rate_limiter.wait_time()
                print(f"  ⏳ Rate limit — waiting {wait:.0f}s")
                await asyncio.sleep(wait)

            # Get cover letter (generate new one if needed)
            cover_letter_path = get_cover_letter_pdf(job, cover_letters)
            if not cover_letter_path:
                cover_letter_path = generate_quick_cover_letter(job)
            print(f"  📄 Cover letter: {cover_letter_path.name if cover_letter_path else 'none'}")

            # Apply
            result = await handle_easy_apply(page, job, cover_letter_path)

            # If external redirect, log and skip (handled in Step 4)
            if result == "external":
                log_application(job_id, company, title, url, "external_redirect", "skipped",
                               "Redirects to external site — will handle in Step 4")
                print(f"  🔗 External redirect — saved for Step 4")
                continue

            # Log
            log_application(job_id, company, title, url, "easy_apply", result)

            if result == "applied":
                applied += 1
                rate_limiter.record_application()
                print(f"  ✅ APPLIED ({applied} total)")
            elif result == "failed":
                failed += 1
                print(f"  ❌ FAILED")
            elif result == "expired":
                print(f"  ⏰ EXPIRED")

            # Delay between applications
            if idx < len(jobs) - 1:
                await inter_application_delay(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)

        # Save session
        await context.storage_state(path=str(STORAGE_STATE))
        await browser.close()

    print(f"\n  Step 3 complete: {applied} applied, {failed} failed")
    print_summary()


# ============================================================
# STEP 4: EXTERNAL APPLICATIONS (by domain)
# ============================================================

async def step4_external_apply():
    """Apply to external jobs in batches by ATS domain."""
    print("\n" + "=" * 60)
    print("  STEP 4: EXTERNAL APPLICATIONS (by domain)")
    print("=" * 60)

    # Load categorized jobs
    cat_path = DATA_DIR / "jobs_categorized.json"
    if not cat_path.exists():
        print("  No categorized jobs found. Running Step 2 first...")
        step2_filter_and_categorize()

    with open(cat_path) as f:
        categorized = json.load(f)

    domains = categorized.get('external_by_domain', {})

    # Also add any LinkedIn jobs that were "external_redirect" from Step 3
    already_applied = get_applied_urls()

    # Process domains in order of size (most jobs first)
    sorted_domains = sorted(domains.items(), key=lambda x: -len(x[1]))

    total_remaining = sum(
        len([j for j in jobs_list if j['url'] not in already_applied])
        for _, jobs_list in sorted_domains
    )

    if total_remaining == 0:
        print("\n  All external jobs already done!")
        return

    print(f"\n  Total external jobs remaining: {total_remaining}")
    print(f"  Domains: {', '.join(f'{d}({len(j)})' for d, j in sorted_domains)}")
    print(f"  Mode: {MODE.upper()}")

    cover_letters = parse_cover_letters()
    ensure_log_exists()
    rate_limiter = RateLimiter(max_per_hour=MAX_APPS_PER_HOUR)

    async with async_playwright() as playwright:
        browser, context = await create_browser_context(playwright)
        page = await context.new_page()

        applied = 0
        failed = 0

        for domain, jobs_list in sorted_domains:
            # Filter already applied
            batch = [j for j in jobs_list if j['url'] not in already_applied]
            if not batch:
                continue

            print(f"\n{'═' * 60}")
            print(f"  DOMAIN: {domain.upper()} ({len(batch)} jobs)")
            print(f"{'═' * 60}")

            for idx, job in enumerate(batch):
                job_id = job.get('id', idx)
                company = job.get('company', 'Unknown')
                title = job.get('title', 'Unknown')
                url = job['url']

                print(f"\n{'─' * 60}")
                print(f"  [{idx+1}/{len(batch)}] #{job_id}: {title[:55]}")
                print(f"  Company: {company} | Domain: {domain}")

                # Rate limiting
                if not rate_limiter.can_apply():
                    wait = rate_limiter.wait_time()
                    print(f"  ⏳ Rate limit — waiting {wait:.0f}s")
                    await asyncio.sleep(wait)

                # Cover letter
                cover_letter_path = get_cover_letter_pdf(job, cover_letters)
                if not cover_letter_path:
                    cover_letter_path = generate_quick_cover_letter(job)

                # Apply
                result = await handle_external_apply(page, job, cover_letter_path)

                # Log
                log_application(job_id, company, title, url, f"external_{domain}", result)

                if result == "applied":
                    applied += 1
                    rate_limiter.record_application()
                    print(f"  ✅ APPLIED ({applied} total)")
                elif result == "failed":
                    failed += 1
                    print(f"  ❌ FAILED")

                # Delay
                if idx < len(batch) - 1:
                    await inter_application_delay(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)

            # Longer break between domains
            print(f"\n  Finished domain '{domain}'. Taking a break...")
            await asyncio.sleep(random.uniform(30, 60))

        await context.storage_state(path=str(STORAGE_STATE))
        await browser.close()

    print(f"\n  Step 4 complete: {applied} applied, {failed} failed")
    print_summary()


# ============================================================
# COVER LETTER GENERATOR (for new jobs without pre-made ones)
# ============================================================

def generate_quick_cover_letter(job: dict) -> Path | None:
    """Generate a cover letter for jobs without a pre-made one."""
    company = job.get('company', 'the company')
    title = job.get('title', 'the role')
    job_id = job.get('id', 0)

    # Use the generic cover letter PDF if it exists
    if GENERIC_COVER_LETTER.exists():
        return GENERIC_COVER_LETTER

    # Otherwise generate a basic one
    try:
        from weasyprint import HTML

        text = f"""Dear Hiring Manager,

I am writing to apply for the {title} position at {company}. With over two years of experience in trade operations at Morgan Stanley's Prime Brokerage, I bring direct expertise in settlement, reconciliation, and trade lifecycle management across equity and fixed income products.

At Morgan Stanley, I managed pre-matching and settlement for hedge fund and institutional clients across US, EMEA, and APAC markets, processing $10M+ in daily trade volume. I built Python and Excel reconciliation tools that improved Straight-Through Processing rates by 10%, resolved complex settlement failures under same-day deadlines, and collaborated with compliance stakeholders on risk escalation and regulatory adherence.

My technical toolkit includes Python (pandas, numpy), advanced Excel (VBA, Power Query), Bloomberg Terminal, and trade management systems (SafeGUI, TM, CTM). I hold an MSc in Investment and Risk Finance (Distinction) from the University of Westminster.

I would welcome the opportunity to contribute my operational expertise and analytical capabilities to your team.

Kind regards,
Nidhi Shetty"""

        pdf_dir = OUTPUT_DIR / "cover_letters"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        safe_company = re.sub(r'[^\w\s-]', '', company).replace(' ', '_')[:30]
        pdf_path = pdf_dir / f"cover_letter_{job_id}_{safe_company}.pdf"

        if pdf_path.exists():
            return pdf_path

        html = f"""<!DOCTYPE html><html><head><style>
            body {{ font-family: Calibri, Arial, sans-serif; font-size: 11pt; line-height: 1.5; margin: 2.5cm; color: #333; }}
            p {{ margin-bottom: 12pt; }}
        </style></head><body>
            {''.join(f'<p>{p}</p>' for p in text.split(chr(10)+chr(10)) if p.strip())}
        </body></html>"""

        HTML(string=html).write_pdf(str(pdf_path))
        return pdf_path
    except Exception:
        return GENERIC_COVER_LETTER if GENERIC_COVER_LETTER.exists() else None


# ============================================================
# MAIN
# ============================================================

async def run_workflow(steps: list[int]):
    """Run specified workflow steps."""
    if 1 in steps:
        await step1_fetch_jobs()

    if 2 in steps:
        step2_filter_and_categorize()

    if 3 in steps:
        await step3_linkedin_easy_apply()

    if 4 in steps:
        await step4_external_apply()

    print("\n" + "=" * 60)
    print("  WORKFLOW COMPLETE")
    print("=" * 60)
    print_summary()


def main():
    parser = argparse.ArgumentParser(description="Job Application Workflow")
    parser.add_argument('--step', type=int, help='Run only this step (1-4)')
    parser.add_argument('--from', type=int, dest='from_step', help='Start from this step')
    args = parser.parse_args()

    if args.step:
        steps = [args.step]
    elif args.from_step:
        steps = list(range(args.from_step, 5))
    else:
        steps = [1, 2, 3, 4]

    print("=" * 60)
    print("  JOB APPLICATION WORKFLOW")
    print(f"  Steps: {steps}")
    print(f"  Mode: {MODE.upper()}")
    print("=" * 60)

    asyncio.run(run_workflow(steps))


if __name__ == "__main__":
    main()
