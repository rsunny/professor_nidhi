# Multi-Agent Architecture Reference

Technical reference for Claude instances modifying or extending the job automation system.

## Agent Inventory

| Agent | File | Model | Max Steps | Responsibility |
|-------|------|-------|-----------|----------------|
| Page Classifier | `agents/page_classifier.py` | Haiku | 1 | Identify page type (login, form, success, expired, captcha) and platform |
| Auth Agent | `agents/auth_agent.py` | Haiku | 12 | Handle OAuth, direct signin, account creation, password resets |
| Navigation Agent | `agents/navigation_agent.py` | Haiku | 5 | Find and click Apply/Next/Submit buttons |
| Workday Form | `agents/workday_agent.py` | Haiku | 20 | Multi-page Workday forms |
| Greenhouse Form | `agents/greenhouse_agent.py` | Haiku | 12 | Greenhouse application forms |
| Lever Form | `agents/lever_agent.py` | Haiku | 15 | Lever.co application forms |
| Generic Form | `agents/generic_form_agent.py` | Haiku | 15 | Fallback for iCIMS, SmartRecruiters, Reed, custom ATS |
| Gmail Agent | `agents/gmail_agent.py` | Haiku | 10 | Extract verification codes from email |
| Easy Apply (legacy) | `linkedin_apply.py` | Opus | 25 | LinkedIn Easy Apply modal (standalone, not in agents/) |

## Orchestration Flow

```
apply_all_jobs.py: process_job(url)
  │
  ├─ 1. Navigate to URL
  │
  ├─ 2. Page Classifier → {page_type, platform, has_apply_button}
  │     ├─ "expired" → log skip, return
  │     ├─ "login_required" → Auth Agent
  │     ├─ "captcha" → log skip, return
  │     └─ "form" or "job_listing" → continue
  │
  ├─ 3. Auth Agent (if login detected)
  │     ├─ Try LinkedIn OAuth button
  │     ├─ Try direct signin (stored credentials)
  │     └─ Try account creation → Gmail for verification
  │
  ├─ 4. Navigation Agent → click Apply button
  │     └─ Handles redirects, new tabs, popups
  │
  └─ 5. Route to Form Agent (by platform)
        ├─ "workday" → Workday Form Agent
        ├─ "greenhouse" → Greenhouse Form Agent
        ├─ "lever" → Lever Form Agent
        └─ default → Generic Form Agent
```

## Shared Tool Set (FORM_TOOLS)

All form-filling agents use these 6 tools (defined in `agents/__init__.py` and `profile_tools.py`):

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `lookup_answer` | `question`, `field_type`, `options` | Resolve field value from Nidhi's profile |
| `fill_field` | `index`, `value` | Type into an indexed form input |
| `select_option` | `index`, `value` | Select dropdown option by visible text |
| `click_element` | `index` | Click button, checkbox, radio, or link |
| `upload_file` | `index`, `file_type` | Upload resume (`resume`) or cover letter (`cover_letter`) |
| `done` | `status`, `reason` | Signal completion: `applied`, `skipped`, `error` |

## Agent Runner Pattern

All agents follow the same execution loop (defined in `agents/__init__.py`):

```python
async def run_agent(page, system_prompt, tools, max_steps, model_tier="haiku"):
    messages = []
    for step in range(max_steps):
        # 1. Get current page state
        elements = await get_interactive_elements(page)

        # 2. Ask Claude what to do
        response = client.messages.create(
            model=resolve_model(model_tier),
            system=system_prompt,
            messages=messages + [{"role": "user", "content": elements}],
            tools=tools
        )

        # 3. Execute tool calls
        for block in response.content:
            if block.type == "tool_use":
                result = await execute_tool_call(page, block.name, block.input)
                if block.name == "done":
                    return AgentResult(success=result["status"] == "applied", ...)

        # 4. Append to conversation history
        messages.append(...)

    return AgentResult(success=False, error="max_steps_exceeded")
```

## Model Tiers

| Tier | Model ID (Bedrock) | Use Case | Cost |
|------|-------------------|----------|------|
| `haiku` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | All form filling, classification, navigation | Lowest |
| `sonnet` | `us.anthropic.claude-sonnet-4-6-v1` | Complex reasoning fallback | Medium |
| `opus` | `us.anthropic.claude-opus-4-6-v1` | LinkedIn Easy Apply only (legacy handler) | Highest |

Resolution via `resolve_model(tier)` in `agents/__init__.py`. Model IDs loaded from `.env`.

## Profile Data System

### Answer Resolution (`profile_tools.py: execute_lookup`)

```
execute_lookup(question, field_type, options)
  │
  ├─ 1. Pattern matching: question_patterns in application_answers.json
  │     e.g., "sponsorship|visa" → "Yes, I require Skilled Worker visa sponsorship"
  │
  ├─ 2. Common-sense rules (hardcoded)
  │     e.g., "willing to relocate" → "Yes" (already in London)
  │     e.g., "criminal record" → "No"
  │
  ├─ 3. Direct profile lookup (keyword → field)
  │     e.g., "email" → personal.email
  │     e.g., "university" → education.university
  │
  ├─ 4. Option best-match (when dropdown options provided)
  │     Fuzzy matches profile values against available options
  │
  └─ 5. Unknown → returns best guess with confidence: "low"
```

### Profile Data Location

- `auto_apply/application_answers.json` — Nidhi's full profile (personal, work auth, employment, salary, education, skills)
- `auto_apply/data/storage_state.json` — LinkedIn session cookies

## How to Add a New Platform Agent

1. **Create agent file**: `auto_apply/agents/{platform}_agent.py`

2. **Define system prompt** describing the platform's form structure:
   ```python
   SYSTEM_PROMPT = """You are filling a {Platform} job application form for Nidhi Shetty.
   Use lookup_answer to get profile data. Fill all visible fields. Click Next/Submit when done.
   Platform-specific notes: [any quirks]..."""
   ```

3. **Set max_steps** based on form complexity (single-page: 12, multi-page: 20)

4. **Export from `agents/__init__.py`**:
   ```python
   from .platform_agent import PlatformFormAgent
   ```

5. **Add routing in `apply_all_jobs.py`**:
   ```python
   elif classification.platform == "platform_name":
       result = await PlatformFormAgent.run(page)
   ```

6. **Add URL detection in page classifier** (`agents/page_classifier.py`):
   ```python
   if "platform.com" in url:
       return PageClassification(platform="platform_name", ...)
   ```

7. **Test** with a single job URL before batch processing.

## Key Design Principles

1. **Tool uniformity**: Every agent uses the same FORM_TOOLS. No agent-specific tools.
2. **Step budgets**: Each agent has a hard limit. Prevents infinite loops and cost runaway.
3. **Heuristic fast-paths**: Page classifier uses URL patterns before calling AI.
4. **Fail fast, log, continue**: Agents return `AgentResult(success=False)` — orchestrator moves on.
5. **Session isolation**: Each job gets a fresh page context. No state leakage between applications.
6. **Model economy**: Haiku for everything except legacy Easy Apply (Opus).
