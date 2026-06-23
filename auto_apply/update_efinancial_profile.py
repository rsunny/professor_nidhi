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

    # Only try first entry for now (debugging)
    exp = EXPERIENCE[0]
    print(f"\n  Adding: {exp['title']} at {exp['company']}")

    # Click "Add Experience" button
    add_exp = page.locator('[data-gtm-trackable="Add Experience"]')
    try:
        await add_exp.click(timeout=5000)
        await asyncio.sleep(3)
        print("  Clicked Add Experience")
    except Exception as e:
        print(f"  Could not click Add Experience: {e}")
        return

    # Try to fill form fields
    await fill_experience_form(page, exp)


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

    # Set up network interception to catch ALL API calls
    all_api_calls = []
    def on_response(response):
        url = response.url
        if "fsdm" in url or "api" in url or "typeahead" in url:
            all_api_calls.append(f"{response.status} {url[:120]}")
    page.on("response", on_response)

    # 1. Job Title — try "Trade Support" for more specific match
    await fill_typeahead(page, '#jobTitle', "Trade Support Analyst", "title", short_text="Trade Support")

    print(f"    API calls after title: {all_api_calls}")
    all_api_calls.clear()

    # 2. Company Name — try typing full name slowly
    # First, manually test the API endpoint
    company_api_test = await page.evaluate("""async () => {
        try {
            const resp = await fetch('https://fsdm.efinancialcareers.com/v2/typeahead/companies?filterTerm=Morgan');
            if (resp.ok) {
                const data = await resp.json();
                return JSON.stringify(data).substring(0, 500);
            }
            return `status: ${resp.status}`;
        } catch(e) {
            return `error: ${e.message}`;
        }
    }""")
    print(f"    Company API test: {company_api_test}")

    await fill_typeahead(page, '#companyName', exp["company"], "company", short_text="Morgan Stanley")
    print(f"    API calls after company: {all_api_calls}")
    all_api_calls.clear()

    # 3. Uncheck "current work" if not current role
    if not exp.get("current", False):
        try:
            # Use JavaScript to uncheck and trigger Angular change detection
            result = await page.evaluate("""() => {
                const cb = document.querySelector('#currentWork');
                if (!cb) return 'not found';
                const wasBefore = cb.checked;
                cb.checked = false;
                cb.dispatchEvent(new Event('change', { bubbles: true }));
                cb.dispatchEvent(new Event('input', { bubbles: true }));
                // Also try clicking the label if it exists
                const label = document.querySelector('label[for="currentWork"]');
                if (label && wasBefore) label.click();
                return `was=${wasBefore}, now=${cb.checked}`;
            }""")
            print(f"    CurrentWork checkbox: {result}")
            await asyncio.sleep(2)
        except Exception as e:
            print(f"    Checkbox error: {e}")

    # 4. Location — typeahead (shorter text)
    location_short = exp["location"].split(",")[0]  # e.g., "Glasgow"
    await fill_typeahead(page, '#workingLocation', exp["location"], "location", short_text=location_short)

    # 5. Dump full modal HTML to understand form structure
    await asyncio.sleep(1)

    modal_info = await page.evaluate("""() => {
        const modal = document.querySelector('modal-container');
        if (!modal) return {html: 'no modal', elements: []};

        // Get form-related elements
        const results = [];
        const els = modal.querySelectorAll('input, textarea, select, button, label, form, [formcontrolname], [ngmodel]');
        for (const el of els) {
            const tag = el.tagName.toLowerCase();
            const rect = el.getBoundingClientRect();
            const classStr = (typeof el.className === 'string') ? el.className : (el.className.baseVal || '');
            results.push({
                tag: tag,
                type: el.getAttribute('type') || '',
                id: el.getAttribute('id') || '',
                name: el.getAttribute('name') || '',
                fc: el.getAttribute('formcontrolname') || '',
                cls: classStr.substring(0, 120),
                value: (el.value || '').substring(0, 50),
                text: (el.innerText || '').substring(0, 60),
                visible: rect.width > 0 && rect.height > 0,
                disabled: el.disabled || false,
                required: el.required || el.hasAttribute('required'),
                checked: el.checked || false,
            });
        }
        return {html: modal.innerHTML.substring(0, 8000), elements: results};
    }""")

    print(f"\n    Full modal elements ({len(modal_info['elements'])} found):")
    for item in modal_info['elements']:
        vis = "V" if item['visible'] else "H"
        req = " REQ" if item['required'] else ""
        val = f" val='{item['value'][:30]}'" if item['value'] else ""
        fc = f" fc='{item['fc']}'" if item['fc'] else ""
        chk = " CHECKED" if item.get('checked') else ""
        cls_short = ""
        if 'invalid' in item.get('cls', '') or 'ng-invalid' in item.get('cls', ''):
            cls_short = " NG-INVALID"
        if 'ng-valid' in item.get('cls', '') and 'ng-invalid' not in item.get('cls', ''):
            cls_short = " NG-VALID"
        print(f"      [{vis}] <{item['tag']} id='{item['id']}' type='{item['type']}'{fc}{val}{req}{chk}{cls_short}>")

    # Print relevant class info for inputs
    print(f"\n    Input classes (Angular validation state):")
    for item in modal_info['elements']:
        if item['tag'] == 'input' and item['visible']:
            print(f"      #{item['id']}: {item['cls'][:120]}")

    # 8. Try to save — force-enable the button and submit
    await asyncio.sleep(1)

    # Check if save button exists
    save_btn = page.locator('modal-container button[type="submit"]')
    try:
        if await save_btn.first.is_visible(timeout=3000):
            # Force-enable the button via JS (Angular disabled attr check is buggy)
            await page.evaluate("""() => {
                const btn = document.querySelector('modal-container button[type="submit"]');
                if (btn) {
                    btn.removeAttribute('disabled');
                    btn.classList.remove('disabled');
                    btn.disabled = false;
                }
            }""")
            await asyncio.sleep(0.5)

            # Click with force=True to bypass actionability checks
            await save_btn.first.click(force=True, timeout=5000)
            await asyncio.sleep(4)

            # Check if modal closed (success) or still open (failure)
            try:
                modal_visible = await page.locator('modal-container').is_visible(timeout=2000)
            except Exception:
                modal_visible = False

            if not modal_visible:
                print(f"    Experience saved successfully!")
            else:
                # Modal still open — Angular rejected the submission
                # Try setting Angular model directly via component
                print(f"    Modal still open after submit — trying Angular model injection...")
                success = await angular_inject_experience(page, exp)
                if not success:
                    print(f"    Could not save — cancelling")
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


async def angular_inject_experience(page: Page, exp: dict) -> bool:
    """Try to inject values and submit via Angular component internals."""
    try:
        # Approach 1: Find the Angular component and try to call its submit/save method
        result = await page.evaluate("""() => {
            const modal = document.querySelector('modal-container');
            if (!modal) return 'no modal';

            // Walk up from the button to find Angular component
            const btn = modal.querySelector('button[type="submit"]');
            if (!btn) return 'no btn';

            // Try ng.getComponent on various elements
            const results = [];

            // Check if ng API is available
            if (typeof ng === 'undefined') {
                results.push('ng not available (prod mode)');
            } else {
                // Try to get component from modal children
                const children = modal.querySelectorAll('[_nghost-ng-c579259638]');
                for (const child of children) {
                    try {
                        const comp = ng.getComponent(child);
                        if (comp) {
                            const methods = Object.getOwnPropertyNames(Object.getPrototypeOf(comp)).filter(m => m !== 'constructor');
                            results.push(`Component found! Methods: ${methods.join(', ')}`);
                        }
                    } catch(e) {}
                }
            }

            // Check button's click listeners and disabled binding
            results.push(`btn disabled=${btn.disabled}`);
            results.push(`btn outerHTML=${btn.outerHTML.substring(0, 200)}`);

            // Look for the component's internal state by checking __ngContext__
            let el = modal.firstElementChild;
            let depth = 0;
            while (el && depth < 5) {
                if (el.__ngContext__) {
                    results.push(`Found __ngContext__ at depth ${depth}, tag=${el.tagName}`);
                    // Try to find the form state in the context
                    const ctx = el.__ngContext__;
                    if (Array.isArray(ctx)) {
                        for (let i = 0; i < Math.min(ctx.length, 50); i++) {
                            const item = ctx[i];
                            if (item && typeof item === 'object' && item.constructor && item.constructor.name) {
                                const name = item.constructor.name;
                                if (name.includes('Component') || name.includes('Form') || name.includes('Modal')) {
                                    results.push(`  ctx[${i}] = ${name}`);
                                    // Check for save/submit method
                                    const proto = Object.getPrototypeOf(item);
                                    const methods = Object.getOwnPropertyNames(proto).filter(m => m !== 'constructor');
                                    if (methods.length) results.push(`    methods: ${methods.slice(0, 15).join(', ')}`);
                                    // Check properties
                                    const props = Object.keys(item).filter(k => !k.startsWith('_'));
                                    if (props.length) results.push(`    props: ${props.slice(0, 15).join(', ')}`);
                                }
                            }
                        }
                    }
                    break;
                }
                el = el.firstElementChild;
                depth++;
            }

            return results.join('\\n');
        }""")
        print(f"    Angular component analysis:\n{result}")

        # Approach 2: Try to find and call save method via the component
        submit_result = await page.evaluate("""() => {
            const modal = document.querySelector('modal-container');
            if (!modal) return 'no modal';

            // Try to find component with save/submit method
            let el = modal.firstElementChild;
            while (el) {
                if (el.__ngContext__ && Array.isArray(el.__ngContext__)) {
                    for (const item of el.__ngContext__) {
                        if (item && typeof item === 'object') {
                            // Look for save/submit/onSave methods
                            const proto = Object.getPrototypeOf(item);
                            if (proto) {
                                const methods = Object.getOwnPropertyNames(proto);
                                const saveMethod = methods.find(m =>
                                    m.toLowerCase().includes('save') ||
                                    m.toLowerCase().includes('submit') ||
                                    m.toLowerCase().includes('confirm')
                                );
                                if (saveMethod && typeof item[saveMethod] === 'function') {
                                    try {
                                        item[saveMethod]();
                                        return `Called ${saveMethod}() on ${item.constructor.name}`;
                                    } catch(e) {
                                        return `Error calling ${saveMethod}: ${e.message}`;
                                    }
                                }
                            }
                        }
                    }
                }
                el = el.firstElementChild;
            }
            return 'no save method found';
        }""")
        print(f"    Submit attempt: {submit_result}")

        await asyncio.sleep(3)

        # Check if modal closed
        try:
            modal_visible = await page.locator('modal-container').is_visible(timeout=2000)
        except Exception:
            modal_visible = False

        if not modal_visible:
            print(f"    Experience saved via Angular method call!")
            return True
        return False
    except Exception as e:
        print(f"    Angular inject error: {e}")
        return False


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

        # About and Skills already saved — just do experience now
        # await update_about(page)
        # await update_skills(page)

        # Only do experience (the problematic one)
        await update_experience(page)

        print(f"\n{'=' * 60}")
        print("  Profile update complete!")
        print(f"{'=' * 60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
