"""Browser management — Playwright setup, session persistence, anti-detection."""

import asyncio
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from config import LINKEDIN_EMAIL, LINKEDIN_PASSWORD, HEADLESS, STORAGE_STATE_PATH


async def create_browser_context(playwright) -> tuple[Browser, BrowserContext]:
    """Launch browser with anti-detection settings and load saved session."""
    browser = await playwright.chromium.launch(
        headless=HEADLESS,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )

    # Context options for anti-detection
    context_opts = {
        "viewport": {"width": 1366, "height": 768},
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "locale": "en-GB",
        "timezone_id": "Europe/London",
    }

    # Load existing session if available
    if STORAGE_STATE_PATH.exists():
        context_opts["storage_state"] = str(STORAGE_STATE_PATH)
        print("[browser] Loaded saved session from storageState.json")

    context = await browser.new_context(**context_opts)

    # Remove webdriver flag
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    """)

    return browser, context


async def ensure_logged_in(page: Page) -> bool:
    """Check if we're logged into LinkedIn. If not, perform login."""
    await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
    await asyncio.sleep(3)

    # Check if we landed on the feed (logged in) or login page
    if "/login" in page.url or "/authwall" in page.url or "linkedin.com/uas" in page.url:
        print("[browser] Not logged in — starting login flow...")
        return await perform_login(page)

    # Check for feed elements
    feed_indicator = await page.query_selector('[data-test-id="feed-sort"], .feed-shared-update-v2, .scaffold-layout')
    if feed_indicator:
        print("[browser] Already logged in!")
        return True

    print("[browser] Unclear state — attempting login...")
    return await perform_login(page)


async def perform_login(page: Page) -> bool:
    """Login to LinkedIn with credentials from .env."""
    if not LINKEDIN_EMAIL or not LINKEDIN_PASSWORD:
        print("[browser] ERROR: LINKEDIN_EMAIL or LINKEDIN_PASSWORD not set in .env")
        print("[browser] Please fill in your credentials and restart.")
        return False

    await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    await asyncio.sleep(2)

    # Fill email
    email_field = await page.query_selector('#username')
    if email_field:
        await email_field.fill(LINKEDIN_EMAIL)
        await asyncio.sleep(0.5)

    # Fill password
    password_field = await page.query_selector('#password')
    if password_field:
        await password_field.fill(LINKEDIN_PASSWORD)
        await asyncio.sleep(0.5)

    # Click sign in
    sign_in_btn = await page.query_selector('[data-litms-control-urn="login-submit"], button[type="submit"]')
    if sign_in_btn:
        await sign_in_btn.click()

    # Wait for navigation — might hit 2FA/captcha
    print("[browser] Waiting for login to complete (handle 2FA manually if prompted)...")
    try:
        # Wait up to 120 seconds for either feed page or checkpoint
        await page.wait_for_url(
            lambda url: "/feed" in url or "/mynetwork" in url or "/in/" in url,
            timeout=120000,
        )
        print("[browser] Login successful!")
        # Save session
        await save_session(page.context)
        return True
    except Exception:
        # Check if we're on a challenge page
        if "checkpoint" in page.url or "challenge" in page.url:
            print("[browser] 2FA/Verification detected — please complete it manually in the browser.")
            print("[browser] Waiting up to 5 minutes for manual verification...")
            try:
                await page.wait_for_url(
                    lambda url: "/feed" in url or "/mynetwork" in url,
                    timeout=300000,
                )
                print("[browser] Verification completed! Login successful.")
                await save_session(page.context)
                return True
            except Exception:
                print("[browser] Timed out waiting for verification.")
                return False
        print(f"[browser] Login may have failed. Current URL: {page.url}")
        return False


async def save_session(context: BrowserContext):
    """Save browser session state for reuse."""
    await context.storage_state(path=str(STORAGE_STATE_PATH))
    print(f"[browser] Session saved to {STORAGE_STATE_PATH}")
