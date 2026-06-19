"""Anti-detection: random delays, scroll simulation, rate limiting."""

from __future__ import annotations

import asyncio
import random
import time
from typing import List
from playwright.async_api import Page

# Track application timestamps for rate limiting
_app_timestamps: List[float] = []


async def random_delay(min_sec: float = 1.0, max_sec: float = 3.0):
    """Sleep for a random duration between min and max seconds."""
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)


async def simulate_reading(page: Page, duration_sec: float = None):
    """Simulate a human reading a page — scroll, pause, scroll back."""
    if duration_sec is None:
        duration_sec = random.uniform(8, 20)

    start = time.time()
    while time.time() - start < duration_sec:
        # Scroll down a bit
        scroll_amount = random.randint(100, 400)
        await page.mouse.wheel(0, scroll_amount)
        await asyncio.sleep(random.uniform(1.5, 4.0))

    # Scroll back to top
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(random.uniform(0.5, 1.5))


async def random_mouse_move(page: Page):
    """Move mouse to random positions to simulate human behavior."""
    for _ in range(random.randint(2, 5)):
        x = random.randint(100, 1200)
        y = random.randint(100, 600)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.1, 0.4))


async def type_like_human(page: Page, selector: str, text: str):
    """Type text character by character with random delays."""
    element = await page.query_selector(selector)
    if element:
        await element.click()
        await asyncio.sleep(0.2)
        for char in text:
            await element.type(char, delay=random.randint(30, 120))
        await asyncio.sleep(0.3)


def check_rate_limit(max_per_hour: int) -> bool:
    """Check if we've exceeded the rate limit. Returns True if OK to proceed."""
    now = time.time()
    one_hour_ago = now - 3600

    # Remove timestamps older than 1 hour
    _app_timestamps[:] = [t for t in _app_timestamps if t > one_hour_ago]

    if len(_app_timestamps) >= max_per_hour:
        wait_time = _app_timestamps[0] - one_hour_ago
        print(f"[rate-limit] Hit {max_per_hour}/hour limit. Wait {wait_time:.0f}s before next application.")
        return False

    return True


def record_application():
    """Record that an application was submitted."""
    _app_timestamps.append(time.time())


async def inter_application_delay(min_sec: int = 30, max_sec: int = 120):
    """Wait between applications with random delay."""
    delay = random.randint(min_sec, max_sec)
    print(f"[humanizer] Waiting {delay}s before next application...")
    await asyncio.sleep(delay)
