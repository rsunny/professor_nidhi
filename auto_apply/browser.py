"""Browser management — Playwright setup with session persistence."""

import asyncio
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from config import LINKEDIN_EMAIL, LINKEDIN_PASSWORD, STORAGE_STATE, OUTPUT_DIR
from humanizer import random_delay


async def create_browser_context(playwright) -> tuple[Browser, BrowserContext]:
    """Launch browser with anti-detection settings and load saved session."""
    browser = await playwright.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )

    # Browser context options
    context_options = {
        "viewport": {"width": 1366, "height": 768},
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "locale": "en-GB",
        "timezone_id": "Europe/London",
    }

    # Load existing session if available
    if STORAGE_STATE.exists():
        context_options["storage_state"] = str(STORAGE_STATE)
        print("  📂 Loaded saved session")

    context = await browser.new_context(**context_options)

    # Remove webdriver flag
    await context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """
    )

    return browser, context


async def ensure_logged_in(context: BrowserContext) -> Page:
    """Check if logged into LinkedIn; if not, perform login."""
    page = await context.new_page()
    await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
    await random_delay(2, 4)

    # Check if we're on the feed (logged in) or redirected to login
    if "/login" in page.url or "/authwall" in page.url:
        print("  🔐 Not logged in — performing login...")
        await perform_login(page)
    else:
        print("  ✅ Already logged in")

    # Save session state
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(STORAGE_STATE))
    return page


async def perform_login(page: Page):
    """Log into LinkedIn. Waits for manual 2FA if triggered."""
    await page.goto(
        "https://www.linkedin.com/login", wait_until="domcontentloaded"
    )
    await random_delay(1, 2)

    # Fill email
    email_input = page.locator('input[name="session_key"]')
    await email_input.fill(LINKEDIN_EMAIL)
    await random_delay(0.5, 1)

    # Fill password
    password_input = page.locator('input[name="session_password"]')
    await password_input.fill(LINKEDIN_PASSWORD)
    await random_delay(0.5, 1)

    # Click sign in
    await page.locator('button[type="submit"]').click()
    await random_delay(2, 4)

    # Check for 2FA / security challenge
    if "checkpoint" in page.url or "challenge" in page.url:
        print("\n  ⚠️  2FA/Security challenge detected!")
        print("  👉 Please complete the verification manually in the browser.")
        print("  ⏳ Waiting up to 120 seconds...")

        # Wait for user to complete 2FA
        try:
            await page.wait_for_url(
                "**/feed/**", timeout=120000
            )
            print("  ✅ 2FA completed successfully!")
        except Exception:
            # Also check if we ended up on any logged-in page
            if "/feed" in page.url or "/jobs" in page.url:
                print("  ✅ Login successful!")
            else:
                raise RuntimeError(
                    "Login failed — could not complete 2FA within 120 seconds"
                )
    elif "/feed" in page.url:
        print("  ✅ Login successful (no 2FA required)")
    else:
        # Wait a bit more in case of slow redirect
        await random_delay(3, 5)
        if "/feed" not in page.url and "/jobs" not in page.url:
            print(f"  ⚠️  Unexpected page after login: {page.url}")
            print("  👉 Please navigate to LinkedIn feed manually.")
            await page.wait_for_url("**/feed/**", timeout=60000)
