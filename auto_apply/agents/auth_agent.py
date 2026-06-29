"""Auth Agent — handles ALL authentication scenarios.

Strategies (tried in order):
1. LinkedIn OAuth ("Sign in with LinkedIn" / "Apply with LinkedIn")
2. Direct sign-in (email + password — account may already exist)
3. Forgot password → Gmail reset link
4. Create new account → Gmail verification

Model: haiku (up to 12 steps per strategy)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from playwright.async_api import Page, BrowserContext

from . import (
    AgentResult, get_client, resolve_model, get_interactive_elements,
    get_page_text, random_delay,
)

# Credentials — different accounts for different purposes
LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")

# For third-party job portal login/signup — uses dedicated job portal email
JOB_PORTAL_EMAIL = os.getenv("JOB_PORTAL_EMAIL", os.getenv("GMAIL_EMAIL", ""))
JOB_PORTAL_PASSWORD = os.getenv("JOB_PORTAL_PASSWORD", os.getenv("GMAIL_PASSWORD", ""))


# ---------------------------------------------------------------------------
# Strategy 1: LinkedIn OAuth
# ---------------------------------------------------------------------------

async def try_linkedin_oauth(page: Page, context: BrowserContext) -> AgentResult:
    """Click "Sign in with LinkedIn" / "Apply with LinkedIn" and handle OAuth popup."""
    selectors = [
        'button:has-text("Apply with LinkedIn")',
        'a:has-text("Apply with LinkedIn")',
        'button:has-text("Sign in with LinkedIn")',
        'a:has-text("Sign in with LinkedIn")',
        'button:has-text("Continue with LinkedIn")',
        'a:has-text("Continue with LinkedIn")',
        'a[href*="linkedin.com/oauth"]',
        'button[class*="linkedin"]',
        'a[class*="linkedin"]',
    ]

    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if not await btn.is_visible(timeout=2000):
                continue

            print("    [auth] Found LinkedIn OAuth button, clicking...")
            pages_before = context.pages[:]
            await btn.click()
            await random_delay(3, 5)

            # Check for new tab (OAuth popup)
            new_pages = [p for p in context.pages if p not in pages_before]
            oauth_page = new_pages[-1] if new_pages else page

            # If on LinkedIn OAuth/login page, enter credentials
            if "linkedin.com" in oauth_page.url.lower():
                oauth_url = oauth_page.url.lower()
                if any(x in oauth_url for x in ["login", "authwall", "uas", "oauth", "authorize"]):
                    print("    [auth] On LinkedIn auth page, signing in...")
                    await _fill_linkedin_credentials(oauth_page)
                    await random_delay(3, 5)

                # Click Allow/Authorize if prompted
                try:
                    allow_btn = oauth_page.locator(
                        'button:has-text("Allow"), button:has-text("Authorize"), '
                        'button:has-text("Continue"), input[value="Allow"]'
                    ).first
                    if await allow_btn.is_visible(timeout=5000):
                        await allow_btn.click()
                        await random_delay(3, 5)
                except Exception:
                    pass

                # Close popup if still open
                if new_pages and not oauth_page.is_closed():
                    try:
                        await oauth_page.close()
                    except Exception:
                        pass

            # Check if we're past login now
            await random_delay(2, 3)
            has_password = await page.evaluate("""() => {
                const pw = document.querySelectorAll('input[type="password"]');
                for (const f of pw) {
                    const r = f.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) return true;
                }
                return false;
            }""")

            if not has_password:
                print("    [auth] LinkedIn OAuth successful!")
                return AgentResult(success=True, status="authenticated", data={"method": "linkedin_oauth"})

        except Exception as e:
            continue

    return AgentResult(success=False, status="oauth_not_available", error="No LinkedIn OAuth option found or failed")


# ---------------------------------------------------------------------------
# Strategy 2: Direct sign-in
# ---------------------------------------------------------------------------

async def try_direct_signin(page: Page) -> AgentResult:
    """Try signing in with job portal credentials (account may already exist)."""
    try:
        # Find email field
        email_field = page.locator(
            'input[type="email"], input[name="email"], input[name="username"], '
            'input[autocomplete="email"], input[autocomplete="username"], '
            'input[id*="email" i], input[id*="user" i], input[id*="login" i]'
        ).first

        if not await email_field.is_visible(timeout=3000):
            return AgentResult(success=False, status="no_email_field", error="No email/username field found")

        print(f"    [auth] Trying direct sign-in with {JOB_PORTAL_EMAIL}...")
        await email_field.fill(JOB_PORTAL_EMAIL)
        await random_delay(0.5, 1)

        # Find and fill password
        pw_field = page.locator('input[type="password"]').first
        if await pw_field.is_visible(timeout=3000):
            await pw_field.fill(JOB_PORTAL_PASSWORD)
            await random_delay(0.5, 1)

        # Click sign-in button
        signin_btn = page.locator(
            'button[type="submit"], button:has-text("Sign In"), button:has-text("Log In"), '
            'button:has-text("Sign in"), button:has-text("Log in"), '
            'input[type="submit"], button:has-text("Continue"), '
            'button:has-text("Next"), button[data-automation-id="signIn"]'
        ).first
        if await signin_btn.is_visible(timeout=3000):
            await signin_btn.click()
            await random_delay(4, 6)

            # Check if we got past login
            pw_still = page.locator('input[type="password"]')
            if not await pw_still.is_visible(timeout=3000):
                print("    [auth] Direct sign-in successful!")
                return AgentResult(success=True, status="authenticated", data={"method": "direct_signin"})

            # Check for error messages
            try:
                body = (await page.inner_text("body")).lower()
                if "incorrect" in body or "invalid" in body or "not found" in body:
                    return AgentResult(success=False, status="invalid_credentials",
                                       error="Credentials rejected")
            except Exception:
                pass

        return AgentResult(success=False, status="signin_failed", error="Sign-in did not resolve")

    except Exception as e:
        return AgentResult(success=False, status="signin_error", error=str(e)[:100])


# ---------------------------------------------------------------------------
# Strategy 3: Forgot password → Gmail reset
# ---------------------------------------------------------------------------

async def try_forgot_password(page: Page, context: BrowserContext) -> AgentResult:
    """Click "Forgot Password", enter email, then retrieve reset link from Gmail."""
    from .gmail_agent import get_gmail_verification

    try:
        # Find forgot password link
        forgot_selectors = [
            'a:has-text("Forgot")', 'a:has-text("Reset")',
            'button:has-text("Forgot")', 'a:has-text("forgot")',
            'a:has-text("reset password")', 'a[href*="forgot"]',
            'a[href*="reset"]',
        ]

        clicked = False
        for sel in forgot_selectors:
            try:
                link = page.locator(sel).first
                if await link.is_visible(timeout=2000):
                    await link.click()
                    await random_delay(2, 3)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            return AgentResult(success=False, status="no_forgot_link", error="No forgot password link found")

        print(f"    [auth] On forgot password page, entering {JOB_PORTAL_EMAIL}...")

        # Fill email for reset
        email_field = page.locator(
            'input[type="email"], input[name="email"], input[name="username"], '
            'input[id*="email" i], input[placeholder*="email" i]'
        ).first
        if await email_field.is_visible(timeout=3000):
            await email_field.fill(JOB_PORTAL_EMAIL)
            await random_delay(0.5, 1)

        # Submit
        submit_btn = page.locator(
            'button[type="submit"], button:has-text("Send"), button:has-text("Reset"), '
            'button:has-text("Submit"), button:has-text("Continue"), button:has-text("Next")'
        ).first
        if await submit_btn.is_visible(timeout=3000):
            await submit_btn.click()
            await random_delay(5, 8)

        # Get reset link from Gmail
        print("    [auth] Checking Gmail for reset link...")
        gmail_result = await get_gmail_verification(context, "password reset")

        if gmail_result and gmail_result.get("link_clicked"):
            print("    [auth] Password reset link followed!")
            # The gmail agent may have set a new password on the reset page
            if gmail_result.get("password_reset"):
                return AgentResult(success=True, status="password_reset",
                                   data={"method": "forgot_password"})

        if gmail_result and gmail_result.get("code"):
            # Enter the code
            code = gmail_result["code"]
            code_field = page.locator(
                'input[name*="code" i], input[name*="token" i], '
                'input[placeholder*="code" i], input[type="text"]'
            ).first
            if await code_field.is_visible(timeout=3000):
                await code_field.fill(code)
                submit = page.locator('button[type="submit"], button:has-text("Verify")').first
                if await submit.is_visible(timeout=2000):
                    await submit.click()
                    await random_delay(3, 5)
                    return AgentResult(success=True, status="code_verified",
                                       data={"method": "forgot_password_code"})

        return AgentResult(success=False, status="reset_failed",
                           error="Could not complete password reset")

    except Exception as e:
        return AgentResult(success=False, status="forgot_error", error=str(e)[:100])


# ---------------------------------------------------------------------------
# Strategy 4: Create new account
# ---------------------------------------------------------------------------

async def try_create_account(page: Page, context: BrowserContext) -> AgentResult:
    """Create a new account using AI-driven form filling."""
    from .gmail_agent import get_gmail_verification

    client = get_client()
    model = resolve_model("haiku")

    system_prompt = f"""You are creating an account on a job application platform.

CREDENTIALS:
- Email: {JOB_PORTAL_EMAIL}
- Password: {JOB_PORTAL_PASSWORD}
- First Name: Nidhi
- Last Name: Shetty
- Phone: +447438416662

INSTRUCTIONS:
1. Find "Create Account", "Sign Up", or "Register" button/link and click it
2. Fill the registration form with credentials above
3. Prefer email signup over social logins
4. After filling, click Submit/Create/Register
5. If verification code needed, signal NEED_VERIFICATION
6. If account created, signal DONE
7. If "already registered" error, signal ALREADY_EXISTS

ACTIONS (respond with ONLY one JSON):
- {{"type": "CLICK", "index": <number>, "description": "what"}}
- {{"type": "FILL", "index": <number>, "value": "text"}}
- {{"type": "SELECT", "index": <number>, "value": "option"}}
- {{"type": "DONE", "reason": "account created"}}
- {{"type": "NEED_VERIFICATION", "reason": "verification code needed"}}
- {{"type": "ALREADY_EXISTS", "reason": "email already registered"}}
- {{"type": "SKIP", "reason": "cannot create account"}}
"""

    messages = []
    for step in range(12):
        try:
            elements = await get_interactive_elements(page)
        except Exception:
            await random_delay(2, 3)
            continue

        messages.append({"role": "user", "content": f"Step {step+1}:\n{elements[:4000]}"})

        try:
            response = client.messages.create(
                model=model,
                max_tokens=300,
                system=system_prompt,
                messages=messages[-6:],
            )
        except Exception as e:
            return AgentResult(success=False, status="api_error", error=str(e)[:100])

        text = response.content[0].text.strip()
        messages.append({"role": "assistant", "content": text})

        action = _parse_action(text)
        if not action:
            continue

        action_type = action.get("type")

        if action_type == "DONE":
            print("    [auth] Account created!")
            return AgentResult(success=True, status="account_created",
                               data={"method": "create_account"})

        elif action_type == "NEED_VERIFICATION":
            print("    [auth] Verification needed, checking Gmail...")
            gmail_result = await get_gmail_verification(context, "verification")
            if gmail_result and gmail_result.get("code"):
                messages.append({
                    "role": "user",
                    "content": f"Verification code from email: {gmail_result['code']}. Enter it now."
                })
                continue
            elif gmail_result and gmail_result.get("link_clicked"):
                return AgentResult(success=True, status="verified_via_link",
                                   data={"method": "create_account"})
            else:
                return AgentResult(success=False, status="verification_failed",
                                   error="No verification email found")

        elif action_type == "ALREADY_EXISTS":
            return AgentResult(success=False, status="already_exists",
                               error="Account already exists")

        elif action_type == "SKIP":
            return AgentResult(success=False, status="cannot_create",
                               error=action.get("reason", "Cannot create account"))

        elif action_type == "CLICK":
            from . import click_element_by_index
            await click_element_by_index(page, action.get("index", 0))
            await random_delay(2, 3)

        elif action_type == "FILL":
            from . import fill_element_by_index
            await fill_element_by_index(page, action.get("index", 0), action.get("value", ""))
            await random_delay(0.5, 1)

        elif action_type == "SELECT":
            from . import select_element_by_index
            await select_element_by_index(page, action.get("index", 0), action.get("value", ""))
            await random_delay(0.5, 1)

    return AgentResult(success=False, status="max_steps", error="Exceeded 12 steps for account creation")


# ---------------------------------------------------------------------------
# Master auth function — tries all strategies in order
# ---------------------------------------------------------------------------

async def authenticate(page: Page, context: BrowserContext, has_linkedin_oauth: bool = False) -> AgentResult:
    """Try all auth strategies in order. Returns first success or final failure.

    Order: LinkedIn OAuth → Direct Sign-in → Forgot Password → Create Account
    """
    # Strategy 1: LinkedIn OAuth (fastest, no new account needed)
    if has_linkedin_oauth:
        result = await try_linkedin_oauth(page, context)
        if result.success:
            return result
        print(f"    [auth] OAuth failed: {result.error}")

    # Strategy 2: Direct sign-in (account may exist)
    result = await try_direct_signin(page)
    if result.success:
        return result
    if result.status == "invalid_credentials":
        print("    [auth] Credentials rejected, trying forgot password...")
    else:
        print(f"    [auth] Direct signin failed: {result.error}")

    # Strategy 3: Forgot password
    result = await try_forgot_password(page, context)
    if result.success:
        return result
    print(f"    [auth] Forgot password failed: {result.error}")

    # Strategy 4: Create new account
    result = await try_create_account(page, context)
    if result.success:
        return result
    print(f"    [auth] Account creation failed: {result.error}")

    return AgentResult(
        success=False, status="all_auth_failed",
        error="All authentication strategies exhausted"
    )


# ---------------------------------------------------------------------------
# LinkedIn-specific sign-in (for LinkedIn pages, not external OAuth)
# ---------------------------------------------------------------------------

async def handle_linkedin_signin(page: Page) -> AgentResult:
    """Handle LinkedIn's own sign-in page (authwall, login redirect)."""
    try:
        await _fill_linkedin_credentials(page)
        await random_delay(4, 6)

        # Check if we got past login
        url = page.url.lower()
        if "/login" not in url and "/authwall" not in url and "/checkpoint" not in url:
            return AgentResult(success=True, status="authenticated", data={"method": "linkedin_signin"})

        # Check for checkpoint/verification
        if "checkpoint" in url or "challenge" in url:
            return AgentResult(success=False, status="checkpoint",
                               error="LinkedIn requires verification/captcha")

        return AgentResult(success=False, status="linkedin_signin_failed",
                           error="Still on login page after credentials")
    except Exception as e:
        return AgentResult(success=False, status="linkedin_error", error=str(e)[:100])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _fill_linkedin_credentials(page: Page):
    """Fill LinkedIn email + password fields and submit."""
    # Email
    email_selectors = ['#username', 'input[name="session_key"]', 'input[type="email"]']
    for sel in email_selectors:
        try:
            field = page.locator(sel).first
            if await field.is_visible(timeout=2000):
                await field.fill(LINKEDIN_EMAIL)
                break
        except Exception:
            continue

    await random_delay(0.5, 1)

    # Password
    pw_selectors = ['#password', 'input[name="session_password"]', 'input[type="password"]']
    for sel in pw_selectors:
        try:
            field = page.locator(sel).first
            if await field.is_visible(timeout=2000):
                await field.fill(LINKEDIN_PASSWORD)
                break
        except Exception:
            continue

    await random_delay(0.5, 1)

    # Submit
    submit_selectors = ['button[type="submit"]', 'button:has-text("Sign in")', 'button:has-text("Log in")']
    for sel in submit_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                break
        except Exception:
            continue


def _parse_action(text: str) -> dict | None:
    """Parse action JSON from text response."""
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except Exception:
            pass

    match = re.search(r'\{[^{}]+\}', text)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None
