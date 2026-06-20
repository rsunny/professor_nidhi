"""Anti-detection: random delays, scroll simulation, mouse movements."""

import asyncio
import random
import time


class RateLimiter:
    """Track application rate and enforce limits."""

    def __init__(self, max_per_hour: int = 5):
        self.max_per_hour = max_per_hour
        self.timestamps: list[float] = []

    def can_apply(self) -> bool:
        """Check if we can apply without exceeding rate limit."""
        now = time.time()
        one_hour_ago = now - 3600
        self.timestamps = [t for t in self.timestamps if t > one_hour_ago]
        return len(self.timestamps) < self.max_per_hour

    def record_application(self):
        """Record that an application was submitted."""
        self.timestamps.append(time.time())

    def wait_time(self) -> float:
        """Seconds to wait before next application is allowed."""
        if self.can_apply():
            return 0
        oldest = min(self.timestamps)
        return (oldest + 3600) - time.time()


async def random_delay(min_sec: float = 1.0, max_sec: float = 3.0):
    """Sleep for a random duration between min and max seconds."""
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)


async def simulate_reading(page, duration_sec: float = None):
    """Simulate reading a page — scroll slowly, pause, scroll back."""
    if duration_sec is None:
        duration_sec = random.uniform(15, 45)

    end_time = time.time() + duration_sec
    viewport_height = page.viewport_size["height"] if page.viewport_size else 800

    while time.time() < end_time:
        # Scroll down a random amount
        scroll_amount = random.randint(100, viewport_height // 2)
        await page.mouse.wheel(0, scroll_amount)
        await asyncio.sleep(random.uniform(1.5, 4.0))

        # Occasionally scroll up slightly
        if random.random() < 0.3:
            await page.mouse.wheel(0, -random.randint(50, 150))
            await asyncio.sleep(random.uniform(0.5, 1.5))


async def random_mouse_move(page):
    """Move mouse to a random position on the page."""
    viewport = page.viewport_size or {"width": 1280, "height": 800}
    x = random.randint(100, viewport["width"] - 100)
    y = random.randint(100, viewport["height"] - 100)
    await page.mouse.move(x, y)
    await asyncio.sleep(random.uniform(0.1, 0.3))


async def human_type(page, selector: str, text: str):
    """Type text with human-like delays between keystrokes."""
    element = page.locator(selector)
    await element.click()
    await asyncio.sleep(random.uniform(0.2, 0.5))

    # Clear existing text
    await element.fill("")
    await asyncio.sleep(random.uniform(0.1, 0.3))

    # Type character by character with varying speed
    for char in text:
        await element.press_sequentially(char, delay=random.randint(30, 120))

    await asyncio.sleep(random.uniform(0.2, 0.5))


async def inter_application_delay(min_sec: float = 30, max_sec: float = 120):
    """Long delay between applications to appear human."""
    delay = random.uniform(min_sec, max_sec)
    print(f"  ⏳ Waiting {delay:.0f}s before next application...")
    await asyncio.sleep(delay)
