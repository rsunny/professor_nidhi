"""eFinancialCareers Job Filter — Keyword + AI relevance check.

Reads efc_jobs_with_descriptions.json, applies keyword filter and AI check,
saves relevant jobs to efc_jobs_filtered.json.

Usage:
    python3 -u filter_efc_jobs.py
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import DATA_DIR
from ai_navigator import get_client

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

EFC_JOBS_WITH_DESC_FILE = DATA_DIR / "efc_jobs_with_descriptions.json"
EFC_JOBS_FILTERED_FILE = DATA_DIR / "efc_jobs_filtered.json"

# ---------------------------------------------------------------------------
# Keyword Filter
# ---------------------------------------------------------------------------

# Positive keywords (must match at least one in title OR description)
POSITIVE_KEYWORDS = [
    r"operat", r"settlement", r"reconcil", r"middle.?office",
    r"trade.?support", r"post.?trade", r"clearing", r"custody",
    r"fund.?account", r"asset.?servic", r"corporate.?action",
    r"treasury", r"fixed.?income", r"derivatives",
    r"prime.?brokerage", r"client.?servic.*(?:finance|bank|invest)",
    r"investment.?operat", r"fund.?operat", r"banking.?operat",
    r"finance.?analyst", r"financial.?analyst",
    r"financial.?operat", r"transfer.?agent", r"collateral",
    r"due.?diligence.*(?:operat|finance)",
    r"brokerage", r"securities", r"payment",
]

# Negative keywords (reject if in title)
NEGATIVE_TITLE_KEYWORDS = [
    r"director", r"\bvp\b", r"vice.?president", r"head\s+of",
    r"chief", r"managing.?director", r"\bcto\b", r"\bcfo\b",
    r"software.?eng", r"developer", r"devops", r"data.?eng",
    r"machine.?learn", r"\bai\b.*(?:eng|develop)", r"full.?stack",
    r"front.?end", r"back.?end", r"java\b", r"python.*(?:developer|engineer)",
    r"architect", r"quantitative.?(?:developer|analyst|researcher)",
    r"nurse", r"doctor", r"teacher", r"lawyer", r"solicitor",
    r"marketing.?manager", r"sales.?director",
    r"contract|day.?rate|interim|freelance",
]


def keyword_filter(job: dict) -> tuple[bool, str]:
    """Apply keyword filter. Returns (pass, reason)."""
    title = (job.get("title", "") or "").lower()
    desc = (job.get("description", "") or "").lower()
    location = (job.get("location", "") or "").lower()
    job_type = (job.get("job_type", "") or "").lower()
    full_text = f"{title} {desc}"

    # Must be London-based
    if location and "london" not in location and "remote" not in location and "uk" not in location:
        return False, "Not London"

    # Check contract/temp
    if "contract" in job_type and "permanent" not in job_type:
        return False, "Contract role"

    # Negative title check
    for pattern in NEGATIVE_TITLE_KEYWORDS:
        if re.search(pattern, title, re.IGNORECASE):
            return False, f"Title reject: {pattern}"

    # Positive keyword check
    for pattern in POSITIVE_KEYWORDS:
        if re.search(pattern, full_text, re.IGNORECASE):
            return True, f"Matched: {pattern}"

    return False, "No positive keyword match"


# ---------------------------------------------------------------------------
# AI Filter
# ---------------------------------------------------------------------------

def ai_relevance_check(client, job: dict) -> tuple[bool, str]:
    """AI relevance check for borderline jobs."""
    title = job.get("title", "")
    desc = job.get("description", "")[:2500]

    prompt = f"""You are filtering jobs for Nidhi Shetty. Her profile:
- Indian national, based in London, needs Skilled Worker visa sponsorship
- 5 years experience total, 2.5 in financial services (Morgan Stanley Prime Brokerage, Glasgow)
- MSc Investment & Risk Finance (Distinction), University of Westminster 2022
- Skills: trade settlement, reconciliation, post-trade operations, Excel/VBA, Bloomberg
- NOT a developer/engineer, no CFA/ACA/ACCA
- Languages: English, Hindi, Marathi (no French/Italian/Korean/Japanese/etc.)

Target roles: Finance operations, trade ops, middle office, settlement, reconciliation
Level: Entry to mid-level (NOT VP, Director, 8+ years required)
Type: PERMANENT only (not contract/temp/FTC)

JOB TITLE: {title}
JOB DESCRIPTION:
{desc}

Should Nidhi apply? REJECT if:
- Requires a language she doesn't speak
- Requires CFA/ACA/ACCA/specific qual she doesn't have
- Is a contract/temp role
- Too senior (8+ years, VP/Director)
- Is a dev/engineering role
- Wrong field entirely

Reply EXACTLY:
APPLY: <reason>
or
REJECT: <reason>"""

    try:
        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1"),
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response.content[0].text.strip()
        if answer.startswith("APPLY"):
            return True, answer
        return False, answer
    except Exception as e:
        return False, f"AI error: {e}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  eFC Job Filter — Keyword + AI")
    print("=" * 60, flush=True)

    if not EFC_JOBS_WITH_DESC_FILE.exists():
        print("  ERROR: efc_jobs_with_descriptions.json not found.")
        print("  Run fetch_efc_descriptions.py first.")
        return

    jobs = json.loads(EFC_JOBS_WITH_DESC_FILE.read_text())
    print(f"  Total jobs loaded: {len(jobs)}")

    # Step 1: Keyword filter
    print(f"\n  Step 1: Keyword filter...\n")
    keyword_passed = []
    reject_reasons = {}

    for job in jobs:
        passed, reason = keyword_filter(job)
        if passed:
            keyword_passed.append(job)
        else:
            category = reason[:30]
            reject_reasons[category] = reject_reasons.get(category, 0) + 1

    print(f"  Keyword filter: {len(keyword_passed)}/{len(jobs)} passed")
    print(f"\n  Top rejection reasons:")
    for reason, count in sorted(reject_reasons.items(), key=lambda x: -x[1])[:10]:
        print(f"    {reason}: {count}")

    # Step 2: AI filter (only on jobs with descriptions)
    print(f"\n  Step 2: AI relevance check on {len(keyword_passed)} jobs...\n")

    client = get_client()
    approved = []
    ai_rejected = 0

    for i, job in enumerate(keyword_passed):
        desc = job.get("description", "")
        if len(desc) < 50:
            # No description — include anyway (we'll check on application)
            job["ai_reason"] = "No description available — included by keyword"
            approved.append(job)
            continue

        is_relevant, reason = ai_relevance_check(client, job)

        if is_relevant:
            job["ai_reason"] = reason
            approved.append(job)
            print(f"    [{i+1}] APPLY: {job['title'][:50]}")
        else:
            ai_rejected += 1

        if (i + 1) % 20 == 0:
            print(f"\n    --- Progress: {i+1}/{len(keyword_passed)} | Approved: {len(approved)} ---\n", flush=True)

    # Save results
    final_jobs = []
    for j in approved:
        final_jobs.append({
            "url": j["url"],
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "location": j.get("location", ""),
            "salary": j.get("salary", ""),
            "job_type": j.get("job_type", ""),
            "description": j.get("description", "")[:1000],
            "ai_reason": j.get("ai_reason", ""),
        })

    EFC_JOBS_FILTERED_FILE.write_text(json.dumps(final_jobs, indent=2))

    print(f"\n{'=' * 60}")
    print(f"  COMPLETE")
    print(f"  Total input: {len(jobs)}")
    print(f"  Keyword passed: {len(keyword_passed)}")
    print(f"  AI approved: {len(approved)}")
    print(f"  AI rejected: {ai_rejected}")
    print(f"  Saved to: {EFC_JOBS_FILTERED_FILE}")
    print(f"\n  Approved jobs:")
    for j in final_jobs[:30]:
        print(f"    - {j['title'][:60]} | {j['company'][:30]}")
    if len(final_jobs) > 30:
        print(f"    ... and {len(final_jobs) - 30} more")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
