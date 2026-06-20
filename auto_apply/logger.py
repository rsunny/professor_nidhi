"""Application tracking — CSV log of results."""

import csv
from datetime import datetime
from pathlib import Path

from config import LOG_FILE, OUTPUT_DIR


def ensure_log_exists():
    """Create the CSV log file with headers if it doesn't exist."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        with open(LOG_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "timestamp",
                    "job_id",
                    "company",
                    "title",
                    "url",
                    "method",
                    "status",
                    "notes",
                ]
            )


def log_application(
    job_id: int,
    company: str,
    title: str,
    url: str,
    method: str,
    status: str,
    notes: str = "",
):
    """Append an application result to the CSV log."""
    ensure_log_exists()
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                datetime.now().isoformat(),
                job_id,
                company,
                title,
                url,
                method,
                status,
                notes,
            ]
        )


def get_applied_urls() -> set[str]:
    """Get set of URLs already applied to (to skip duplicates on re-run).
    Only skips jobs confirmed as submitted. 'scanned' = form filled but not
    submitted in previous test runs, so those should be retried."""
    if not LOG_FILE.exists():
        return set()

    applied = set()
    with open(LOG_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = row.get("status", "")
            # Only skip jobs that were actually submitted successfully
            if status in ("applied", "submitted"):
                applied.add(row.get("url", ""))
    return applied


def print_summary():
    """Print a summary of all applications."""
    if not LOG_FILE.exists():
        print("\n📊 No applications logged yet.")
        return

    stats = {"applied": 0, "failed": 0, "skipped": 0, "expired": 0, "review": 0}

    with open(LOG_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = row["status"]
            stats[status] = stats.get(status, 0) + 1

    total = sum(stats.values())
    print(f"\n{'='*50}")
    print(f"📊 APPLICATION SUMMARY")
    print(f"{'='*50}")
    print(f"  Total processed: {total}")
    print(f"  ✅ Applied:      {stats.get('applied', 0)}")
    print(f"  ❌ Failed:       {stats.get('failed', 0)}")
    print(f"  ⏭️  Skipped:      {stats.get('skipped', 0)}")
    print(f"  ⏰ Expired:      {stats.get('expired', 0)}")
    print(f"  👁️  Review:       {stats.get('review', 0)}")
    print(f"{'='*50}\n")
