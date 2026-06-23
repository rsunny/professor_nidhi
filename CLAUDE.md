# Nidhi Job Automation — System Instructions

## User Context
- User: Prasanth (helping Nidhi Shetty find jobs in London, UK)
- Nidhi: Ex-Morgan Stanley Prime Brokerage, MSc Investment & Risk Finance, 5 yrs experience
- Target roles: Trade operations, middle office, settlement, reconciliation, finance analyst
- Salary minimum: £50,000
- Visa: Skilled Worker (expires April 2027), needs sponsorship

## Decision Rules (Don't Ask User — Just Do It)
1. **Scraping/fetching**: Full autonomy. Fetch as many relevant jobs as possible.
2. **Filtering**: Jobs must pass relevance check (finance/ops keywords) + salary >= £50k.
3. **Applying**: Auto-submit (MODE=auto). No manual review needed.
4. **Errors**: If a job fails, log it and move on. Don't stop the pipeline.
5. **Rate limits**: 5 apps/hour on LinkedIn, 30-120s delays. Reed has no limit but be respectful.
6. **Context compression**: If context gets large, compress and continue. NEVER pause to ask user.
7. **Priority order**: LinkedIn Easy Apply first (fastest), then Reed, then external career pages.

## Current Goals (2026-06-21)
- [x] Fix Easy Apply modal detection (uses `get_dialog_elements()`)
- [x] Fix model ID to use AWS Bedrock via env var
- [x] Add `f_AL=true` for LinkedIn Easy Apply filter
- [ ] Fetch 500+ LinkedIn Easy Apply jobs → `data/jobs_easy_apply_raw.json`
- [ ] Fetch 500+ Reed jobs → `data/jobs_reed_raw.json`
- [ ] Apply to all fetched jobs (auto mode)
- [ ] Explore https://github.com/browser-use/browser-use for future automation

## Key Files
| File | Purpose |
|------|---------|
| `auto_apply/fetch_easy_apply.py` | Scrape LinkedIn Easy Apply jobs (DOM-based) |
| `auto_apply/fetch_reed_jobs.py` | Scrape Reed.co.uk jobs |
| `auto_apply/run_full_pipeline.py` | Master script: fetch + apply all |
| `auto_apply/workflow.py` | Step 2-4 orchestrator (categorize → apply) |
| `auto_apply/linkedin_apply.py` | Easy Apply handler (modal-scoped AI agent) |
| `auto_apply/external_apply.py` | External ATS handler (Workday/Greenhouse/Reed/etc) |
| `auto_apply/job_scraper.py` | Original AI-driven scraper (slower, more thorough) |
| `auto_apply/ai_navigator.py` | Core browser automation agent |
| `auto_apply/.env` | Credentials + config (MODE=auto, Bedrock keys) |
| `auto_apply/data/` | All job JSON files |
| `auto_apply/output/` | Application logs + screenshots |

## Architecture Notes
- **Browser**: Playwright (headless=False for debugging, can switch to headless for unattended)
- **AI Model**: AWS Bedrock `us.anthropic.claude-opus-4-6-v1` (via anthropic SDK with bedrock)
- **LinkedIn login**: Uses saved `storage_state.json` (cookies persist between runs)
- **Easy Apply flow**: Click button → detect modal → `get_dialog_elements()` scopes to `[role="dialog"]` → AI fills → auto-submit
- **Reed flow**: Direct DOM scraping, `handle_reed()` in external_apply.py

## Anti-Detection
- Random delays (30-120s between apps, 10-25s between searches)
- Human-like mouse moves + reading simulation
- Breaks every 8-10 queries (45-90s)
- Rate limiter: max 5 apps/hour on LinkedIn

## If Something Breaks
1. Check screenshots in `output/screenshots/`
2. Check `output/applications_log.csv` for failure patterns
3. LinkedIn session expired → delete `storage_state.json`, re-login
4. API errors → check `.env` for AWS keys
5. Modal not found → LinkedIn may have changed DOM, check `[role="dialog"]` selector
