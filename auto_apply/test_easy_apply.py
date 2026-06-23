"""Test AI-powered Easy Apply on a specific job URL."""
import asyncio
import sys
sys.path.insert(0, '.')

from playwright.async_api import async_playwright
from config import RESUME_PATH
from browser import create_browser_context, ensure_logged_in
from linkedin_apply import handle_easy_apply


async def test():
    url = "https://www.linkedin.com/jobs/view/4430459620/"
    job = {
        "id": 9999,
        "url": url,
        "company": "Test Company",
        "title": "Easy Apply Test Job",
    }

    print(f"Testing AI-powered Easy Apply on: {url}")
    print(f"Resume: {RESUME_PATH}")
    print("=" * 60)

    async with async_playwright() as playwright:
        browser, context = await create_browser_context(playwright)
        page = await ensure_logged_in(context)

        result = await handle_easy_apply(page, job, None)
        print(f"\n{'=' * 60}")
        print(f"  FINAL RESULT: {result}")
        print(f"{'=' * 60}")

        from config import STORAGE_STATE
        await context.storage_state(path=str(STORAGE_STATE))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(test())
