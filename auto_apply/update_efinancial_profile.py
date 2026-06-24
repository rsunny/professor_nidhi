"""Update eFinancialCareers Profile — About, Skills, Experience sections.

Uses the Angular app's data-gtm-trackable attributes to find edit buttons.

Usage:
    python3 -u update_efinancial_profile.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "data"
EFC_STORAGE_FILE = DATA_DIR / "efinancial_storage_state.json"

EMAIL = os.getenv("EFINANCE_EMAIL", "")
PASSWORD = os.getenv("EFINANCE_PASSWORD", "")

# ---------------------------------------------------------------------------
# Profile Data
# ---------------------------------------------------------------------------

ABOUT_TEXT = """Results-driven finance professional with 5 years of experience, including 2.5 years in financial services at Morgan Stanley's Prime Brokerage division in Glasgow. Skilled in trade settlement, reconciliation, post-trade operations, and client service within institutional financial services.

Currently transitioning back to financial services from an advertising account management role. Strong foundation in investment operations, derivatives processing, and middle-office workflows. MSc in Investment and Risk Finance (Distinction) from the University of Westminster.

Core competencies include trade lifecycle management, break resolution, corporate actions processing, and regulatory reporting. Proficient in Bloomberg Terminal, Refinitiv Eikon, CTM (DTCC), advanced Excel/VBA, and familiar with Python for data analysis.

Seeking trade operations, middle office, settlement, or finance analyst roles in London. Eligible for Skilled Worker visa sponsorship."""

SKILLS_LIST = [
    "Trade Settlement",
    "Reconciliation",
    "Post-Trade Operations",
    "Middle Office",
    "Prime Brokerage",
    "Corporate Actions",
    "Trade Lifecycle Management",
    "Break Resolution",
    "Client Service",
    "Bloomberg Terminal",
    "Refinitiv Eikon",
    "CTM (DTCC)",
    "Excel/VBA",
    "Financial Reporting",
    "Risk Assessment",
    "Regulatory Reporting",
    "Derivatives Processing",
    "Fund Administration",
    "Investment Operations",
    "Python",
    "Bank Reconciliation",
    "Financial Modeling",
]

EXPERIENCE = [
    {
        "title": "Trade Support Analyst - Prime Brokerage",
        "company": "Morgan Stanley",
        "location": "Glasgow, UK",
        "start_month": "September",
        "start_year": "2022",
        "end_month": "April",
        "end_year": "2024",
        "current": False,
        "description": "Supported Prime Brokerage trade operations including trade settlement, reconciliation, and break resolution for institutional clients. Processed equity, fixed income, and derivatives trades across global markets. Used Bloomberg, CTM, and Refinitiv Eikon for trade matching and settlement. Managed corporate actions and client queries. Performed daily P&L reconciliation and regulatory reporting.",
    },
    {
        "title": "Operations Analyst",
        "company": "Morgan Stanley",
        "location": "Glasgow, UK",
        "start_month": "January",
        "start_year": "2022",
        "end_month": "September",
        "end_year": "2022",
        "current": False,
        "description": "Supported middle office operations for Prime Brokerage division. Performed trade reconciliation, managed settlement exceptions, and resolved trade breaks. Assisted in regulatory reporting and client onboarding documentation.",
    },
]


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def login(page: Page) -> bool:
    """Login to eFinancialCareers."""
    await page.goto("https://www.efinancialcareers.co.uk/login", wait_until="domcontentloaded")
    await asyncio.sleep(3)

    if "/login" not in page.url.lower():
        print("  Already logged in!")
        return True

    try:
        await page.fill('#email', EMAIL)
        await asyncio.sleep(0.5)
        await page.fill('#password', PASSWORD)
        await asyncio.sleep(0.5)
        await page.click('button.submit')
        await asyncio.sleep(5)

        if "/login" not in page.url.lower():
            print("  Logged in successfully!")
            state = await page.context.storage_state()
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            EFC_STORAGE_FILE.write_text(json.dumps(state))
            return True
        else:
            print("  Login may have failed — still on login page")
            return False
    except Exception as e:
        print(f"  Login error: {e}")
        return False


# ---------------------------------------------------------------------------
# About Section
# ---------------------------------------------------------------------------

async def update_about(page: Page):
    """Click edit on About section and update text."""
    print("\n  --- Updating ABOUT section ---")

    # Click the edit icon for About section
    edit_about = page.locator('[data-gtm-trackable="Edit About"]')
    try:
        await edit_about.click(timeout=5000)
        await asyncio.sleep(3)
        print("  Clicked Edit About")
    except Exception as e:
        print(f"  Could not click Edit About: {e}")
        return

    # After clicking, explore what appeared (modal, inline form, etc.)
    # Look for textarea
    textarea = page.locator('textarea')
    try:
        count = await textarea.count()
        print(f"  Found {count} textareas after clicking edit")

        if count > 0:
            # Find the visible one
            for i in range(count):
                el = textarea.nth(i)
                if await el.is_visible():
                    await el.fill(ABOUT_TEXT)
                    await asyncio.sleep(1)
                    print("  Filled About textarea")
                    break
            else:
                print("  No visible textarea found")
                await explore_current_state(page)
                return

            # Click Save button
            save_btn = page.locator('button:has-text("Save"), button:has-text("Update"), button[type="submit"]')
            try:
                for i in range(await save_btn.count()):
                    btn = save_btn.nth(i)
                    if await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(3)
                        print("  About saved!")
                        return
                print("  No visible save button found")
            except Exception as e:
                print(f"  Save error: {e}")
        else:
            # Maybe it's contenteditable or inline input
            await explore_current_state(page)
    except Exception as e:
        print(f"  About update error: {e}")
        await explore_current_state(page)


# ---------------------------------------------------------------------------
# Skills Section
# ---------------------------------------------------------------------------

async def update_skills(page: Page):
    """Click edit on Skills section and update."""
    print("\n  --- Updating SKILLS section ---")

    # Click the edit icon for Skills section
    edit_skills = page.locator('[data-gtm-trackable="Edit Skills"]')
    try:
        await edit_skills.click(timeout=5000)
        await asyncio.sleep(3)
        print("  Clicked Edit Skills")
    except Exception as e:
        print(f"  Could not click Edit Skills: {e}")
        return

    # After clicking, look for skill input
    await explore_current_state(page)

    # Try to find skill input field
    skill_input = page.locator('input[type="text"], input[placeholder*="skill"], input[placeholder*="Skill"], input[placeholder*="type"], input[placeholder*="add"], input[placeholder*="search"]')
    try:
        count = await skill_input.count()
        print(f"  Found {count} text inputs")

        visible_input = None
        for i in range(count):
            el = skill_input.nth(i)
            if await el.is_visible():
                visible_input = el
                placeholder = await el.get_attribute("placeholder") or ""
                print(f"  Using input with placeholder: '{placeholder}'")
                break

        if visible_input:
            for skill in SKILLS_LIST:
                await visible_input.fill(skill)
                await asyncio.sleep(0.5)
                await visible_input.press("Enter")
                await asyncio.sleep(0.5)
            print(f"  Added {len(SKILLS_LIST)} skills")

            # Save
            save_btn = page.locator('button:has-text("Save"), button:has-text("Done"), button:has-text("Update")')
            for i in range(await save_btn.count()):
                btn = save_btn.nth(i)
                if await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(3)
                    print("  Skills saved!")
                    return
        else:
            print("  No visible skill input found")
    except Exception as e:
        print(f"  Skills update error: {e}")


# ---------------------------------------------------------------------------
# Experience Section
# ---------------------------------------------------------------------------

async def update_experience(page: Page):
    """Add experience entries."""
    print("\n  --- Updating EXPERIENCE section ---")

    for exp in EXPERIENCE:
        print(f"\n  Adding: {exp['title']} at {exp['company']}")

        # Click "Add Experience" button
        add_exp = page.locator('[data-gtm-trackable="Add Experience"]')
        try:
            await add_exp.click(timeout=5000)
            await asyncio.sleep(3)
            print("  Clicked Add Experience")
        except Exception as e:
            print(f"  Could not click Add Experience: {e}")
            continue

        # Fill the form
        await fill_experience_form(page, exp)

        # Navigate back to profile for next entry
        await page.goto("https://www.efinancialcareers.co.uk/myefc/profile", wait_until="domcontentloaded")
        await asyncio.sleep(3)


async def fill_efc_dropdown(page: Page, label_text: str, value: str):
    """Fill a custom efc-form-dropdown-input by clicking the toggle and selecting a value.

    These dropdowns use Angular's dropdown directive with dropdowntoggle divs.
    """
    try:
        # Find the dropdown toggle that contains the matching label text
        # The structure is: <div dropdowntoggle>...<label>Start Month *</label>...</div>
        toggle = page.locator(f'[dropdowntoggle]:has(label:has-text("{label_text}"))')

        if not await toggle.is_visible(timeout=3000):
            # Try alternative: find the label and get its parent toggle
            toggle = page.locator(f'label:has-text("{label_text}")').locator('..')
            if not await toggle.is_visible(timeout=2000):
                print(f"    {label_text}: dropdown toggle not found")
                return

        # Click to open the dropdown
        await toggle.click()
        await asyncio.sleep(1)

        # Look for the dropdown menu items
        # ngx-bootstrap dropdowns typically render items in a ul.dropdown-menu or similar
        dropdown_item = page.locator(f'.dropdown-menu li:has-text("{value}"), .dropdown-menu button:has-text("{value}"), .dropdown-menu a:has-text("{value}"), [role="menu"] [role="menuitem"]:has-text("{value}"), .dropdown-menu .dropdown-item:has-text("{value}")')

        try:
            if await dropdown_item.first.is_visible(timeout=3000):
                await dropdown_item.first.click()
                await asyncio.sleep(0.5)
                print(f"    {label_text}: selected '{value}'")
                return
        except Exception:
            pass

        # Try broader search - any clickable element with the value text inside the dropdown
        all_items = page.locator(f'.dropdown-menu *:has-text("{value}")')
        try:
            for i in range(await all_items.count()):
                item = all_items.nth(i)
                if await item.is_visible():
                    tag = await item.evaluate("el => el.tagName")
                    if tag in ['LI', 'A', 'BUTTON', 'SPAN', 'DIV']:
                        await item.click()
                        await asyncio.sleep(0.5)
                        print(f"    {label_text}: selected '{value}' (tag={tag})")
                        return
        except Exception:
            pass

        # If nothing found, list what IS in the dropdown
        menu_items = await page.evaluate("""() => {
            const menus = document.querySelectorAll('.dropdown-menu, [role="menu"], ul[class*="dropdown"]');
            const items = [];
            for (const menu of menus) {
                if (menu.getBoundingClientRect().height > 0) {
                    const children = menu.querySelectorAll('li, a, button, [role="menuitem"]');
                    for (const child of children) {
                        const text = (child.innerText || '').trim();
                        if (text) items.push(text.substring(0, 40));
                    }
                }
            }
            return items;
        }""")
        if menu_items:
            print(f"    {label_text}: could not find '{value}', available: {menu_items[:10]}")
        else:
            print(f"    {label_text}: no dropdown menu appeared after click")

        # Close dropdown by pressing Escape
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)

    except Exception as e:
        print(f"    {label_text} dropdown error: {e}")


async def fill_typeahead(page: Page, selector: str, value: str, field_name: str, short_text: str = "") -> bool:
    """Fill an Angular typeahead field by typing and selecting from suggestions.

    Args:
        short_text: Optional shorter text to type for triggering suggestions (e.g., "Morgan" instead of "Morgan Stanley")
    """
    el = page.locator(selector)
    try:
        if not await el.is_visible(timeout=3000):
            print(f"    {field_name}: not visible")
            return False

        # Click to focus
        await el.click()
        await asyncio.sleep(0.5)

        # Clear existing value using keyboard (more reliable than .fill for Angular)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
        await asyncio.sleep(0.3)

        # Type partial text to trigger suggestions (shorter = more suggestions)
        search_text = short_text or value
        await page.keyboard.type(search_text, delay=100)
        await asyncio.sleep(2.5)  # Wait for debounce + API response

        # Look for typeahead dropdown suggestions
        suggestion_selectors = [
            'typeahead-container button',
            'typeahead-container .dropdown-item',
            'typeahead-container li',
            'ngb-typeahead-window button',
            '.dropdown-menu.show li',
            '.dropdown-menu.show a',
            'ul.dropdown-menu li a',
            '[role="listbox"] [role="option"]',
        ]

        for sug_sel in suggestion_selectors:
            suggestion = page.locator(sug_sel)
            try:
                sug_count = await suggestion.count()
                if sug_count > 0:
                    first_vis = False
                    for i in range(min(sug_count, 5)):
                        if await suggestion.nth(i).is_visible(timeout=500):
                            first_vis = True
                            # Try to find the best matching suggestion
                            best_idx = 0
                            for j in range(min(sug_count, 10)):
                                try:
                                    text = await suggestion.nth(j).inner_text()
                                    if value.lower() in text.lower():
                                        best_idx = j
                                        break
                                except Exception:
                                    pass
                            await suggestion.nth(best_idx).click()
                            await asyncio.sleep(0.5)
                            final_val = await el.input_value()
                            print(f"    {field_name}: selected '{final_val[:40]}' from dropdown ({sug_count} options)")
                            return True
                    if not first_vis:
                        continue
            except Exception:
                continue

        # No dropdown appeared — try keyboard Down+Enter
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(0.5)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.5)

        # Check if value was set by checking the input value length
        final_val = await el.input_value()
        if len(final_val) > len(search_text):
            print(f"    {field_name}: keyboard-selected '{final_val[:40]}'")
            return True

        # Still no luck — dispatch events as last resort
        await page.evaluate("""(selector) => {
            const el = document.querySelector(selector);
            if (el) {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }
        }""", selector)
        await asyncio.sleep(0.5)
        print(f"    {field_name}: typed '{search_text}' (no dropdown found)")
        return True

    except Exception as e:
        print(f"    {field_name} error: {e}")
        return False


async def fill_experience_form(page: Page, exp: dict):
    """Fill an experience form with the given data.

    The fields are Angular typeahead inputs — need to type + select from dropdown.
    """
    # Wait for modal to fully render
    await asyncio.sleep(2)

    # 1. Job Title — typeahead
    # Use the actual title to search, but only first few words for the typeahead trigger
    title_search = exp["title"].split(" - ")[0].split()[:2]  # e.g., ["Trade", "Support"] or ["Operations", "Analyst"]
    await fill_typeahead(page, '#jobTitle', exp["title"].split(" - ")[0], "title", short_text=" ".join(title_search))

    # 2. Company Name — plain text input (no typeahead)
    company_input = page.locator('#companyName')
    try:
        await company_input.click()
        await asyncio.sleep(0.3)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(exp["company"], delay=50)
        await asyncio.sleep(0.5)
        # Blur to trigger validation
        await page.keyboard.press("Tab")
        await asyncio.sleep(0.5)
        print(f"    company: typed '{exp['company']}'")
    except Exception as e:
        print(f"    company error: {e}")

    # 3. Fill DATES — these are custom efc-form-dropdown-input components
    # They use dropdowntoggle divs (NOT native select elements)
    # Need to click to open, then select from dropdown menu

    # Start Month
    await fill_efc_dropdown(page, "Start Month", exp.get("start_month", "September"))
    # Start Year
    await fill_efc_dropdown(page, "Start Year", exp.get("start_year", "2022"))

    # 4. Uncheck "current work" if not current role (to show end dates)
    if not exp.get("current", False):
        current_cb = page.locator('#currentWork')
        try:
            if await current_cb.is_visible(timeout=2000):
                is_checked = await current_cb.is_checked()
                if is_checked:
                    await current_cb.click()
                    await asyncio.sleep(1)
                    print(f"    Unchecked 'current work'")
                else:
                    print(f"    CurrentWork already unchecked")
        except Exception as e:
            print(f"    Checkbox error: {e}")

        await asyncio.sleep(1)

        # End Month
        await fill_efc_dropdown(page, "End Month", exp.get("end_month", "April"))
        # End Year
        await fill_efc_dropdown(page, "End Year", exp.get("end_year", "2024"))

    # 5. Location — typeahead
    location_short = exp["location"].split(",")[0]  # e.g., "Glasgow"
    await fill_typeahead(page, '#workingLocation', exp["location"], "location", short_text=location_short)

    # 5. Check if save button is enabled now
    await asyncio.sleep(1)

    btn_disabled = await page.evaluate("""() => {
        const btn = document.querySelector('modal-container button[type="submit"]');
        return btn ? btn.disabled : null;
    }""")
    print(f"    Save button disabled: {btn_disabled}")

    # 8. Try to save
    await asyncio.sleep(1)

    save_btn = page.locator('modal-container button[type="submit"]')
    try:
        if await save_btn.first.is_visible(timeout=3000):
            is_disabled = await save_btn.first.is_disabled()
            if is_disabled:
                print(f"    Save still disabled — force-enabling...")
                await page.evaluate("""() => {
                    const btn = document.querySelector('modal-container button[type="submit"]');
                    if (btn) { btn.removeAttribute('disabled'); btn.disabled = false; }
                }""")
                await asyncio.sleep(0.5)

            await save_btn.first.click(force=True, timeout=10000)
            await asyncio.sleep(4)

            # Check if modal closed
            try:
                modal_visible = await page.locator('modal-container').is_visible(timeout=2000)
            except Exception:
                modal_visible = False

            if not modal_visible:
                print(f"    Experience saved successfully!")
            else:
                print(f"    Save failed — cancelling")
                cancel_btn = page.locator('button:has-text("Cancel")')
                if await cancel_btn.is_visible():
                    await cancel_btn.click()
                    await asyncio.sleep(2)
    except Exception as e:
        print(f"    Save error: {e}")
        cancel_btn = page.locator('button:has-text("Cancel")')
        try:
            if await cancel_btn.is_visible():
                await cancel_btn.click()
                await asyncio.sleep(2)
        except Exception:
            pass




# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

async def explore_current_state(page: Page):
    """Print current interactive elements for debugging."""
    elements = await page.evaluate("""() => {
        const results = [];
        const selectors = [
            'input:not([type="hidden"])', 'textarea', 'select', 'button',
            '[role="button"]', '[role="dialog"]', '[contenteditable]',
        ];
        const allElements = document.querySelectorAll(selectors.join(', '));
        for (const el of allElements) {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            const tag = el.tagName.toLowerCase();
            const type = el.getAttribute('type') || '';
            const name = el.getAttribute('name') || '';
            const id = el.getAttribute('id') || '';
            const placeholder = el.getAttribute('placeholder') || '';
            const fc = el.getAttribute('formcontrolname') || '';
            const text = (el.innerText || el.textContent || '').trim().substring(0, 60);
            const value = el.value || '';
            let desc = `<${tag}`;
            if (type) desc += ` type="${type}"`;
            if (name) desc += ` name="${name}"`;
            if (id) desc += ` id="${id}"`;
            if (fc) desc += ` fc="${fc}"`;
            if (placeholder) desc += ` ph="${placeholder}"`;
            if (value && tag !== 'button') desc += ` val="${value.substring(0, 30)}"`;
            if (text && ['button', 'select'].includes(tag)) desc += ` text="${text.substring(0, 40)}"`;
            desc += '>';
            results.push(desc);
        }
        return results.join('\\n');
    }""")
    print(f"\n  Current elements:\n{elements[:3000]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("  eFinancialCareers Profile Updater")
    print("=" * 60, flush=True)

    if not EMAIL or not PASSWORD:
        print("  ERROR: EFINANCE_EMAIL/EFINANCE_PASSWORD not set in .env")
        return

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        context_options = {
            "viewport": {"width": 1366, "height": 768},
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "locale": "en-GB",
            "timezone_id": "Europe/London",
        }

        if EFC_STORAGE_FILE.exists():
            context_options["storage_state"] = str(EFC_STORAGE_FILE)
            print("  Loaded saved eFC session")

        context = await browser.new_context(**context_options)
        page = await context.new_page()

        # Login
        if not await login(page):
            print("  FAILED to login. Exiting.")
            await browser.close()
            return

        # Navigate to profile
        await page.goto("https://www.efinancialcareers.co.uk/myefc/profile", wait_until="domcontentloaded")
        await asyncio.sleep(5)
        print(f"  On profile page: {page.url}")

        # Update all sections
        await update_about(page)

        # Re-navigate for skills
        await page.goto("https://www.efinancialcareers.co.uk/myefc/profile", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await update_skills(page)

        # Re-navigate for experience
        await page.goto("https://www.efinancialcareers.co.uk/myefc/profile", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await update_experience(page)

        print(f"\n{'=' * 60}")
        print("  Profile update complete!")
        print(f"{'=' * 60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
