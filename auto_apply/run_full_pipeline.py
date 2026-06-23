"""Full pipeline: Fetch 1000+ jobs (LinkedIn + Reed), then apply to all.
Designed to run unattended for 8+ hours."""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, '.')
from config import DATA_DIR


async def main():
    start = time.time()
    print("=" * 60)
    print("  FULL PIPELINE — FETCH 1000+ JOBS & APPLY")
    print("  Target: 500+ LinkedIn Easy Apply + 500+ Reed")
    print("=" * 60, flush=True)

    # Step 1: Fetch LinkedIn Easy Apply jobs
    print("\n\n" + "=" * 60)
    print("  PHASE 1: FETCHING LINKEDIN EASY APPLY JOBS")
    print("=" * 60, flush=True)

    result = subprocess.run(
        [sys.executable, "fetch_easy_apply.py"],
        capture_output=False,
        text=True,
    )
    if result.returncode != 0:
        print(f"  WARNING: LinkedIn fetch exited with code {result.returncode}")

    # Step 2: Fetch Reed jobs
    print("\n\n" + "=" * 60)
    print("  PHASE 2: FETCHING REED JOBS")
    print("=" * 60, flush=True)

    result = subprocess.run(
        [sys.executable, "fetch_reed_jobs.py"],
        capture_output=False,
        text=True,
    )
    if result.returncode != 0:
        print(f"  WARNING: Reed fetch exited with code {result.returncode}")

    # Step 3: Summary
    print("\n\n" + "=" * 60)
    print("  FETCH SUMMARY")
    print("=" * 60, flush=True)

    linkedin_path = DATA_DIR / "jobs_easy_apply_raw.json"
    reed_path = DATA_DIR / "jobs_reed_raw.json"

    linkedin_count = 0
    reed_count = 0
    if linkedin_path.exists():
        with open(linkedin_path) as f:
            linkedin_count = len(json.load(f))
    if reed_path.exists():
        with open(reed_path) as f:
            reed_count = len(json.load(f))

    print(f"  LinkedIn Easy Apply: {linkedin_count} jobs")
    print(f"  Reed: {reed_count} jobs")
    print(f"  Total: {linkedin_count + reed_count} jobs")

    # Step 4: Apply to LinkedIn Easy Apply jobs
    print("\n\n" + "=" * 60)
    print("  PHASE 3: APPLYING TO LINKEDIN EASY APPLY JOBS")
    print("=" * 60, flush=True)

    result = subprocess.run(
        [sys.executable, "workflow.py", "--step", "3"],
        capture_output=False,
        text=True,
    )

    # Step 5: Apply to external (Reed) jobs
    print("\n\n" + "=" * 60)
    print("  PHASE 4: APPLYING TO REED/EXTERNAL JOBS")
    print("=" * 60, flush=True)

    result = subprocess.run(
        [sys.executable, "workflow.py", "--step", "4"],
        capture_output=False,
        text=True,
    )

    elapsed = time.time() - start
    hours = elapsed / 3600
    print(f"\n\n{'=' * 60}")
    print(f"  PIPELINE COMPLETE")
    print(f"  Total time: {hours:.1f} hours")
    print(f"{'=' * 60}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
