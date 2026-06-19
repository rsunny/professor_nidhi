"""Application tracking — CSV log of results."""

import csv
from datetime import datetime
from pathlib import Path
from config import APPLICATIONS_LOG_PATH


FIELDNAMES = [
    "timestamp",
    "job_id",
    "company",
    "title",
    "url",
    "method",
    "status",
    "notes",
]


def init_log():
    """Create the CSV log file with headers if it doesn't exist."""
    if not APPLICATIONS_LOG_PATH.exists():
        with open(APPLICATIONS_LOG_PATH, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()


def log_application(job: dict, method: str, status: str, notes: str = ""):
    """Log an application attempt."""
    init_log()
    with open(APPLICATIONS_LOG_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerow({
            "timestamp": datetime.now().isoformat(),
            "job_id": job.get("id", ""),
            "company": job.get("company", ""),
            "title": job.get("title", ""),
            "url": job.get("url", ""),
            "method": method,
            "status": status,
            "notes": notes,
        })


def get_applied_job_ids() -> set[int]:
    """Get set of job IDs that have already been successfully applied to."""
    if not APPLICATIONS_LOG_PATH.exists():
        return set()

    applied = set()
    with open(APPLICATIONS_LOG_PATH, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") == "applied":
                try:
                    applied.add(int(row["job_id"]))
                except (ValueError, KeyError):
                    pass
    return applied


def print_summary():
    """Print a summary of all application attempts."""
    if not APPLICATIONS_LOG_PATH.exists():
        print("\n[summary] No applications logged yet.")
        return

    stats = {"applied": 0, "failed": 0, "skipped": 0, "expired": 0, "scanned": 0}
    with open(APPLICATIONS_LOG_PATH, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = row.get("status", "unknown")
            stats[status] = stats.get(status, 0) + 1

    total = sum(stats.values())
    print(f"\n{'='*50}")
    print(f"APPLICATION SUMMARY")
    print(f"{'='*50}")
    print(f"  Total attempts: {total}")
    print(f"  ✅ Applied:     {stats.get('applied', 0)}")
    print(f"  ❌ Failed:      {stats.get('failed', 0)}")
    print(f"  ⏭️  Skipped:     {stats.get('skipped', 0)}")
    print(f"  ⏳ Expired:     {stats.get('expired', 0)}")
    print(f"  🔍 Scanned:     {stats.get('scanned', 0)}")
    print(f"{'='*50}\n")
