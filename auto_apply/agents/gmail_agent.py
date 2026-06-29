"""Gmail Agent — retrieves verification codes and clicks confirmation links.

Opens Gmail in a new tab, searches for recent verification/reset emails,
extracts codes or follows links.

Model: No AI needed — pure Playwright automation.
Max time: 30 seconds.
"""

from __future__ import annotations

import asyncio
import os
import re
from playwright.async_api import BrowserContext

from . import random_delay

GMAIL_EMAIL = os.getenv("GMAIL_EMAIL", "")
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD", "")
# Password to set when resetting via email link (for job portals)
JOB_PORTAL_PASSWORD = os.getenv("JOB_PORTAL_PASSWORD", GMAIL_PASSWORD)


async def get_gmail_verification(context: BrowserContext, purpose: str = "verification") -> dict | None:
    """Open Gmail, find latest verification email, extract code or click link.

    Args:
        context: Browser context (will open new tab)
        purpose: What we're looking for ("verification", "password reset", "confirmation")

    Returns:
        {"code": "123456"} — if a numeric code was found
        {"link_clicked": True, "password_reset": bool} — if a link was followed
        None — if nothing found
    """
    gmail_page = await context.new_page()
    try:
        print(f"    [gmail] Opening Gmail to find {purpose} email...")
        await gmail_page.goto("https://mail.google.com", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)

        # Sign in if needed
        if "accounts.google.com" in gmail_page.url.lower():
            signed_in = await _gmail_signin(gmail_page)
            if not signed_in:
                print("    [gmail] Could not sign into Gmail")
                return None

        # Wait for inbox
        await asyncio.sleep(3)

        # Search for recent verification emails
        search_queries = [
            "newer_than:1h subject:(verify OR confirm OR reset OR code OR password OR activate)",
            f"newer_than:1h (verification OR confirmation OR reset)",
            "newer_than:2h subject:(verify OR code OR OTP)",
        ]

        for query in search_queries:
            result = await _search_and_extract(gmail_page, query)
            if result:
                return result

        print("    [gmail] No verification email found")
        return None

    except Exception as e:
        print(f"    [gmail] Error: {str(e)[:80]}")
        return None
    finally:
        try:
            if not gmail_page.is_closed():
                await gmail_page.close()
        except Exception:
            pass


async def _gmail_signin(page) -> bool:
    """Sign into Gmail with stored credentials."""
    try:
        # Email
        email_input = page.locator('input[type="email"]')
        if await email_input.is_visible(timeout=5000):
            await email_input.fill(GMAIL_EMAIL)
            await page.locator('#identifierNext, button:has-text("Next")').first.click()
            await asyncio.sleep(3)

        # Password
        pw_input = page.locator('input[type="password"]')
        if await pw_input.is_visible(timeout=5000):
            await pw_input.fill(GMAIL_PASSWORD)
            await page.locator('#passwordNext, button:has-text("Next")').first.click()
            await asyncio.sleep(5)

        # Check if we're in inbox
        return "mail.google.com" in page.url.lower()
    except Exception:
        return False


async def _search_and_extract(page, query: str) -> dict | None:
    """Search Gmail and extract verification info from first result."""
    try:
        # Click search
        search_input = page.locator('input[aria-label="Search mail"], input[name="q"]').first
        if not await search_input.is_visible(timeout=3000):
            return None

        await search_input.fill(query)
        await page.keyboard.press("Enter")
        await asyncio.sleep(3)

        # Click first email
        first_email = page.locator('tr.zA, div[role="row"], tr[role="row"]').first
        if not await first_email.is_visible(timeout=5000):
            return None

        await first_email.click()
        await asyncio.sleep(2)

        # Get email body
        body = await page.inner_text("body")

        # Try to find verification code (4-8 digit number)
        # Look for patterns like "code: 123456" or "Your code is 123456"
        code_patterns = [
            r'(?:code|otp|pin|token)\s*(?:is|:)\s*(\d{4,8})',
            r'(\d{6})\s*(?:is your|as your)',
            r'verification\s*(?:code|number)\s*(?:is|:)\s*(\d{4,8})',
            r'\b(\d{6})\b',  # Fallback: any 6-digit number
        ]

        for pattern in code_patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                code = match.group(1)
                print(f"    [gmail] Found code: {code}")
                return {"code": code}

        # Try to find verification/reset link
        verify_link = await page.evaluate("""() => {
            const links = document.querySelectorAll('a[href]');
            for (const link of links) {
                const href = link.href || '';
                const text = (link.innerText || '').toLowerCase();
                if ((text.includes('verify') || text.includes('confirm') ||
                     text.includes('activate') || text.includes('reset') ||
                     text.includes('set password') || text.includes('click here') ||
                     text.includes('complete'))
                    && href.startsWith('http')
                    && !href.includes('unsubscribe')
                    && !href.includes('mailto')
                    && !href.includes('google.com')) {
                    return href;
                }
            }
            // Check for styled buttons (often verification links)
            const styledLinks = document.querySelectorAll('a[style*="background"], a[style*="color"]');
            for (const link of styledLinks) {
                const href = link.href || '';
                if (href.startsWith('http') && !href.includes('unsubscribe')
                    && !href.includes('google.com') && !href.includes('mailto')) {
                    return href;
                }
            }
            return null;
        }""")

        if verify_link:
            print(f"    [gmail] Found link, following...")
            await page.goto(verify_link, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)

            # Check if it's a password reset page
            pw_fields = page.locator('input[type="password"]')
            if await pw_fields.first.is_visible(timeout=3000):
                print("    [gmail] Password reset page — setting password...")
                pw_count = await pw_fields.count()
                for i in range(pw_count):
                    await pw_fields.nth(i).fill(JOB_PORTAL_PASSWORD)
                    await asyncio.sleep(0.3)

                # Submit
                submit = page.locator(
                    'button[type="submit"], button:has-text("Reset"), '
                    'button:has-text("Save"), button:has-text("Set Password"), '
                    'button:has-text("Change"), button:has-text("Update"), '
                    'button:has-text("Confirm")'
                ).first
                try:
                    if await submit.is_visible(timeout=3000):
                        await submit.click()
                        await asyncio.sleep(3)
                        print("    [gmail] Password reset completed!")
                except Exception:
                    pass
                return {"link_clicked": True, "password_reset": True}

            return {"link_clicked": True, "password_reset": False}

        return None

    except Exception:
        return None
