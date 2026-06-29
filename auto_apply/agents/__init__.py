"""Multi-agent job application system — shared base and utilities.

Each agent has a focused responsibility, its own step budget, and model choice.
The orchestrator (apply_all_jobs.py) coordinates them with pure Python logic.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import Page

# Re-export key utilities from ai_navigator for agents to use
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_navigator import (
    get_client,
    get_interactive_elements,
    click_element_by_index,
    fill_element_by_index,
    select_element_by_index,
    upload_file_by_index,
    dismiss_overlays,
)
from profile_tools import execute_lookup, set_current_job, get_cover_letter_for_job
from humanizer import random_delay
from config import RESUME_PATH, DATA_DIR, OUTPUT_DIR


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

def resolve_model(tier: str = "haiku") -> str:
    """Resolve model name based on tier and environment config.

    Tiers:
    - haiku: Fast, cheap ($0.25/1M input). For classification, navigation, simple forms.
    - sonnet: Balanced. For complex reasoning when haiku fails.
    - opus: Most capable. Only for LinkedIn Easy Apply (existing) or very complex tasks.
    """
    if tier == "haiku":
        return os.getenv("FORM_FILL_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    elif tier == "sonnet":
        return os.getenv("SONNET_MODEL", "us.anthropic.claude-sonnet-4-6-v1")
    elif tier == "opus":
        return os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-4-6-v1")
    return os.getenv("FORM_FILL_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")


# ---------------------------------------------------------------------------
# AgentResult — structured output from any agent
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """Structured result from any agent."""
    success: bool
    status: str  # "applied", "page_complete", "login_required", "expired", etc.
    data: dict = field(default_factory=dict)
    error: str | None = None
    steps_used: int = 0
    cost_input_tokens: int = 0
    cost_output_tokens: int = 0

    @property
    def failed(self) -> bool:
        return not self.success


# ---------------------------------------------------------------------------
# Tool execution — shared by all form-filling agents
# ---------------------------------------------------------------------------

async def execute_tool_call(page: Page, tool_name: str, tool_input: dict,
                            resume_path: str = "", cover_letter_path: str = "") -> str:
    """Execute a single tool call on a page. Returns result string."""
    try:
        if tool_name == "lookup_answer":
            question = tool_input.get("question", "")
            field_type = tool_input.get("field_type", "text")
            options = tool_input.get("options")
            return execute_lookup(question, field_type, options)

        elif tool_name == "fill_field":
            idx = tool_input.get("index", 0)
            value = tool_input.get("value", "")
            success = await fill_element_by_index(page, idx, value)
            await random_delay(0.3, 0.8)
            return "filled successfully" if success else "ERROR: element not found at index"

        elif tool_name == "select_option":
            idx = tool_input.get("index", 0)
            value = tool_input.get("value", "")
            success = await select_element_by_index(page, idx, value)
            await random_delay(0.3, 0.8)
            return "selected successfully" if success else "ERROR: element not found"

        elif tool_name == "click_element":
            idx = tool_input.get("index", 0)
            success = await click_element_by_index(page, idx)
            await random_delay(1.5, 3.0)
            return "clicked successfully" if success else "ERROR: element not found"

        elif tool_name == "upload_file":
            idx = tool_input.get("index", 0)
            file_type = tool_input.get("file_type", "resume")
            path = cover_letter_path if file_type == "cover_letter" else resume_path
            if not path or not Path(path).exists():
                path = str(RESUME_PATH)
            success = await upload_file_by_index(page, idx, path)
            await random_delay(1, 2)
            return f"uploaded {file_type}" if success else "ERROR: upload failed"

        elif tool_name == "done":
            status = tool_input.get("status", "unknown")
            reason = tool_input.get("reason", "")
            return f"DONE:{status}:{reason}"

        else:
            return f"ERROR: unknown tool '{tool_name}'"

    except Exception as e:
        return f"ERROR: {str(e)[:150]}"


# ---------------------------------------------------------------------------
# Generic agent runner — all tool-based agents use this pattern
# ---------------------------------------------------------------------------

FORM_TOOLS = [
    {
        "name": "lookup_answer",
        "description": (
            "Look up the correct answer for a form question from the applicant's profile. "
            "Call this for EVERY form field you need to fill."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question text or field label"},
                "field_type": {
                    "type": "string",
                    "enum": ["text", "number", "select", "radio", "checkbox", "textarea", "upload"],
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Available options for select/radio/checkbox",
                },
            },
            "required": ["question", "field_type"],
        },
    },
    {
        "name": "fill_field",
        "description": "Fill a text/number/textarea input field with a value",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Element index from page state"},
                "value": {"type": "string", "description": "Value to enter"},
            },
            "required": ["index", "value"],
        },
    },
    {
        "name": "select_option",
        "description": "Select a dropdown option by visible text",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Element index of the <select>"},
                "value": {"type": "string", "description": "Option text to select"},
            },
            "required": ["index", "value"],
        },
    },
    {
        "name": "click_element",
        "description": "Click a button, radio button, checkbox, or link",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Element index to click"},
                "description": {"type": "string", "description": "What you're clicking and why"},
            },
            "required": ["index"],
        },
    },
    {
        "name": "upload_file",
        "description": "Upload resume or cover letter to a file input",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Element index of file input/button"},
                "file_type": {
                    "type": "string",
                    "enum": ["resume", "cover_letter"],
                    "description": "Which file to upload",
                },
            },
            "required": ["index", "file_type"],
        },
    },
    {
        "name": "done",
        "description": "Signal completion or that you need to stop",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["page_complete", "submitted", "applied", "skipped", "expired", "stuck", "error"],
                },
                "reason": {"type": "string", "description": "Explanation"},
                "fields_filled": {"type": "integer", "description": "Number of fields filled this page"},
            },
            "required": ["status", "reason"],
        },
    },
]


async def run_agent(
    page: Page,
    system_prompt: str,
    tools: list[dict],
    max_steps: int,
    model_tier: str = "haiku",
    resume_path: str = "",
    cover_letter_path: str = "",
    context_window: int = 8,
) -> AgentResult:
    """Generic agent loop — all tool-based agents use this.

    Args:
        page: Playwright page to operate on
        system_prompt: Agent's focused system prompt
        tools: Tool definitions (subset of FORM_TOOLS)
        max_steps: Maximum API round-trips
        model_tier: "haiku", "sonnet", or "opus"
        resume_path: Path to resume PDF
        cover_letter_path: Path to cover letter PDF
        context_window: How many recent messages to keep (prevents context overflow)

    Returns:
        AgentResult with success/status/data
    """
    client = get_client()
    model = resolve_model(model_tier)
    messages = []
    total_input_tokens = 0
    total_output_tokens = 0

    for step in range(max_steps):
        # Get fresh page state each step
        try:
            elements = await get_interactive_elements(page)
        except Exception as e:
            return AgentResult(
                success=False, status="error",
                error=f"Cannot read page: {str(e)[:100]}",
                steps_used=step,
            )

        if not elements.strip():
            await random_delay(1, 2)
            continue

        page_url = page.url
        messages.append({
            "role": "user",
            "content": f"Page URL: {page_url}\n\nInteractive elements:\n{elements[:5000]}"
        })

        # Call the model with only recent context to stay lean
        try:
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                messages=messages[-context_window:],
                tools=tools,
            )
        except Exception as e:
            return AgentResult(
                success=False, status="api_error",
                error=f"API call failed: {str(e)[:150]}",
                steps_used=step,
            )

        total_input_tokens += getattr(response.usage, 'input_tokens', 0)
        total_output_tokens += getattr(response.usage, 'output_tokens', 0)

        messages.append({"role": "assistant", "content": response.content})

        # Process response
        if response.stop_reason == "tool_use":
            tool_results = []
            done_result = None

            for block in response.content:
                if block.type != "tool_use":
                    continue

                result_str = await execute_tool_call(
                    page, block.name, block.input,
                    resume_path=resume_path,
                    cover_letter_path=cover_letter_path,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

                # Check if this was a "done" call
                if block.name == "done":
                    done_result = block.input

            messages.append({"role": "user", "content": tool_results})

            if done_result:
                status = done_result.get("status", "unknown")
                reason = done_result.get("reason", "")
                is_success = status in ("submitted", "applied", "page_complete")
                return AgentResult(
                    success=is_success,
                    status=status,
                    data={
                        "reason": reason,
                        "fields_filled": done_result.get("fields_filled", 0),
                    },
                    steps_used=step + 1,
                    cost_input_tokens=total_input_tokens,
                    cost_output_tokens=total_output_tokens,
                )

        elif response.stop_reason == "end_turn":
            # Model stopped without using tools — might have a text response
            # Check if it's signaling something
            text_content = ""
            for block in response.content:
                if hasattr(block, 'text'):
                    text_content += block.text

            if "DONE" in text_content.upper() or "SUBMITTED" in text_content.upper():
                return AgentResult(
                    success=True, status="applied",
                    data={"reason": text_content[:200]},
                    steps_used=step + 1,
                )

    # Exhausted steps
    return AgentResult(
        success=False, status="max_steps",
        error=f"Exhausted {max_steps} steps",
        steps_used=max_steps,
        cost_input_tokens=total_input_tokens,
        cost_output_tokens=total_output_tokens,
    )


# ---------------------------------------------------------------------------
# Page text helper (for classifier and other non-tool agents)
# ---------------------------------------------------------------------------

async def get_page_text(page: Page, max_chars: int = 3000) -> str:
    """Get visible text from page body, truncated."""
    try:
        text = await page.inner_text("body")
        return text[:max_chars]
    except Exception:
        return ""


async def check_success_indicators(page: Page) -> bool:
    """Check if current page shows application success."""
    try:
        body = (await page.inner_text("body")).lower()
        indicators = [
            "thank you for applying",
            "application submitted",
            "application received",
            "successfully submitted",
            "application has been submitted",
            "thank you for your application",
            "we have received your application",
            "your application has been received",
            "application complete",
        ]
        return any(ind in body for ind in indicators)
    except Exception:
        return False
