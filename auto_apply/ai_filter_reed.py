"""AI Filter for Reed Jobs — Uses saved descriptions, no HTTP needed.

Reads the 1,217 keyword-filtered jobs from reed_jobs_ready_to_apply.json,
loads their full descriptions from reed_jobs_with_descriptions.json,
runs AI relevance check on each, and saves truly relevant ones.

Usage:
    python3 -u ai_filter_reed.py
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import DATA_DIR
from ai_navigator import get_client

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REED_READY_FILE = DATA_DIR / "reed_jobs_ready_to_apply.json"
REED_DESCRIPTIONS_FILE = DATA_DIR / "reed_jobs_with_descriptions.json"
REED_FINAL_FILE = DATA_DIR / "reed_jobs_final_filtered.json"


# ---------------------------------------------------------------------------
# AI Filter
# ---------------------------------------------------------------------------

def check_relevance(client, title: str, description: str) -> tuple[bool, str]:
    """AI relevance check — catches language requirements, specific qualifications, etc."""

    prompt = f"""You are filtering jobs for Nidhi Shetty. Her profile:
- Indian national, based in London, needs Skilled Worker visa sponsorship
- Languages: English (fluent), Hindi (fluent), Marathi (fluent), German (basic)
- Does NOT speak: Korean, French, Italian, Spanish, Mandarin, Japanese, Arabic, Dutch, Portuguese
- 5 years experience total, 2.5 in financial services (Morgan Stanley Prime Brokerage, Glasgow)
- Current role: Advertising Account Manager (non-finance, wants to return to finance)
- MSc Investment & Risk Finance (Distinction), University of Westminster 2022
- Skills: trade settlement, reconciliation, post-trade operations, Excel/VBA (advanced), Bloomberg, CTM, Refinitiv Eikon
- Python: Beginner only (NOT a developer)
- No accounting qualifications (not ACA, ACCA, CIMA qualified)
- Not a CFA charterholder
- NOT a software engineer/developer

She is looking for:
- PERMANENT, FULL-TIME roles (not contracts, not day-rate, not temp, not FTC)
- Finance operations, trade operations, middle office, settlement, reconciliation
- Finance analyst, investment operations, fund operations, treasury operations
- Entry to mid-level (NOT VP, Director, Head of, 10+ years required)
- London-based or remote/hybrid

JOB TITLE: {title}

JOB DESCRIPTION:
{description[:2500]}

Should Nidhi apply? REJECT if:
- Requires a language she doesn't speak (Korean, French, Italian, etc.)
- Requires specific qualifications she doesn't have (ACA/ACCA/CIMA/CFA)
- Is a contract/temp/day-rate role
- Is too senior (requires 8+ years, VP/Director level)
- Is primarily a software development/engineering role
- Is in wrong field (IT support, marketing, legal, healthcare, construction, HR)
- Requires specific experience she doesn't have (e.g., "5+ years pensions experience")
- Is not London/remote

Reply with EXACTLY one line:
APPLY: <brief reason why it's a good fit>
or
REJECT: <brief reason why not>"""

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
        # On error, skip (conservative — don't waste browser time on uncertain jobs)
        return False, f"AI error: {e}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  AI FILTER: Deep relevance check on 1,217 jobs")
    print("=" * 60, flush=True)

    # Load keyword-filtered jobs
    ready_jobs = json.loads(REED_READY_FILE.read_text())
    print(f"  Keyword-filtered jobs: {len(ready_jobs)}")

    # Load descriptions
    desc_jobs = json.loads(REED_DESCRIPTIONS_FILE.read_text())
    url_to_desc = {j["url"]: j.get("description", "") for j in desc_jobs}
    print(f"  Descriptions loaded: {len(url_to_desc)}")

    # Match descriptions to filtered jobs
    jobs_with_desc = []
    for job in ready_jobs:
        desc = url_to_desc.get(job["url"], "")
        if desc and len(desc) > 50:
            job["description"] = desc
            jobs_with_desc.append(job)

    print(f"  Jobs with descriptions: {len(jobs_with_desc)}")

    # AI filter
    print(f"\n  Running AI filter...\n", flush=True)

    client = get_client()
    approved = []
    rejected_reasons = {}

    for i, job in enumerate(jobs_with_desc):
        title = job.get("title", "")
        desc = job.get("description", "")

        is_relevant, reason = check_relevance(client, title, desc)

        if is_relevant:
            job["ai_reason"] = reason
            approved.append(job)
            print(f"    [{i+1}] APPLY: {title[:55]} — {reason[7:60]}")
        else:
            # Track rejection categories
            category = reason.split(":")[1][:40].strip() if ":" in reason else reason[:40]
            rejected_reasons[category] = rejected_reasons.get(category, 0) + 1

        if (i + 1) % 20 == 0:
            print(f"\n    --- Progress: {i+1}/{len(jobs_with_desc)} | Approved: {len(approved)} ---\n", flush=True)

    # Save results
    final_jobs = []
    for j in approved:
        final_jobs.append({
            "url": j["url"],
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "sponsors_visa": j.get("sponsors_visa", False),
            "ai_reason": j.get("ai_reason", ""),
        })

    REED_FINAL_FILE.write_text(json.dumps(final_jobs, indent=2))

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"  COMPLETE")
    print(f"  Total checked: {len(jobs_with_desc)}")
    print(f"  APPROVED (ready to apply): {len(approved)}")
    print(f"  Rejected: {len(jobs_with_desc) - len(approved)}")
    print(f"\n  Top rejection reasons:")
    for reason, count in sorted(rejected_reasons.items(), key=lambda x: -x[1])[:15]:
        print(f"    {reason}: {count}")

    sponsor_count = sum(1 for j in final_jobs if j.get("sponsors_visa"))
    print(f"\n  Sponsorship-friendly: {sponsor_count}")
    print(f"  Saved to: {REED_FINAL_FILE}")
    print(f"\n  APPROVED JOBS:")
    for j in final_jobs:
        tag = " [SPONSORS]" if j.get("sponsors_visa") else ""
        print(f"    - {j['title'][:60]}{tag}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
