"""Debug: dump the Easy Apply modal DOM structure to understand LinkedIn's layout."""
import asyncio
import sys
sys.path.insert(0, '.')

from playwright.async_api import async_playwright
from browser import create_browser_context, ensure_logged_in
from humanizer import random_delay


async def debug():
    url = "https://www.linkedin.com/jobs/view/4335196385/"

    async with async_playwright() as playwright:
        browser, context = await create_browser_context(playwright)
        page = await ensure_logged_in(context)

        print("Navigating to job...")
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(8)

        # Click Easy Apply
        easy_apply = page.locator('a:has-text("Easy Apply"), button:has-text("Easy Apply")').first
        await easy_apply.click()
        await asyncio.sleep(5)

        # Now dump the modal structure
        print("\n" + "=" * 60)
        print("MODAL INNER HTML (first 8000 chars):")
        print("=" * 60)

        # Try multiple selectors for the modal
        for selector in ['.jobs-easy-apply-modal', '.artdeco-modal', '[data-test-modal]', '.jobs-easy-apply-content']:
            modal = page.locator(selector).first
            try:
                if await modal.is_visible(timeout=2000):
                    html = await modal.inner_html()
                    print(f"\n--- Selector: {selector} ---")
                    print(html[:8000])
                    break
            except:
                continue

        # Also dump all interactive elements using the ai_navigator approach
        print("\n" + "=" * 60)
        print("INTERACTIVE ELEMENTS:")
        print("=" * 60)
        from ai_navigator import get_interactive_elements
        elements = await get_interactive_elements(page)
        print(elements)

        # Dump the accessibility tree of the modal
        print("\n" + "=" * 60)
        print("ACCESSIBILITY SNAPSHOT:")
        print("=" * 60)
        try:
            acc = await page.accessibility.snapshot()
            from ai_navigator import format_accessibility_tree
            print(format_accessibility_tree(acc)[:5000])
        except Exception as e:
            print(f"Error: {e}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(debug())
