# Production Runbook

Operational guide for running the job application pipeline.

## Quick Start

```bash
cd /Users/prasanthsunny/Downloads/nidhi/auto_apply

# 1. Check LinkedIn session is valid
python3 -c "import json; s=json.load(open('data/storage_state.json')); print(f'{len(s.get(\"cookies\",[]))} cookies loaded')"

# 2. Fetch LinkedIn Easy Apply jobs
python3 -u fetch_easy_apply.py

# 3. Fetch Reed jobs
python3 -u fetch_reed_jobs.py

# 4. Apply to all (multi-agent)
python3 -u apply_all_jobs.py

# 5. Check results
tail -20 output/apply_all_log.csv
```

## Environment Variables (.env)

| Variable | Purpose | Example |
|----------|---------|---------|
| `LINKEDIN_EMAIL` | Login email | `nidhishettyuk23@gmail.com` |
| `LINKEDIN_PASSWORD` | Login password | `***` |
| `RESUME_PATH` | Path to resume PDF | `/path/to/Nidhi_Shetty_CV.pdf` |
| `COVER_LETTER_DIR` | Generated cover letters | `./output/cover_letters_generated/` |
| `GENERIC_COVER_LETTER` | Fallback cover letter PDF | `/path/to/generic_cl.pdf` |
| `MODE` | `auto` (submit) or `review` (pause) | `auto` |
| `MAX_APPS_PER_HOUR` | LinkedIn rate limit | `5` |
| `MIN_DELAY_SECONDS` | Minimum wait between apps | `30` |
| `MAX_DELAY_SECONDS` | Maximum wait between apps | `120` |
| `ANTHROPIC_MODEL` | Primary model (Opus) | `us.anthropic.claude-opus-4-6-v1` |
| `FORM_FILL_MODEL` | Form filling model (Haiku) | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `SONNET_MODEL` | Intermediate model | `us.anthropic.claude-sonnet-4-6-v1` |
| `USE_BEDROCK` | Use AWS Bedrock (`1`) or direct API (`0`) | `0` |
| `AWS_ACCESS_KEY_ID` | Bedrock access key | `AKIA...` |
| `AWS_SECRET_ACCESS_KEY` | Bedrock secret key | `***` |
| `AWS_REGION` | Bedrock region | `us-east-1` |

## Pipeline Scripts (Execution Order)

### Fetching

| Script | Source | Output | Jobs/Run |
|--------|--------|--------|----------|
| `fetch_easy_apply.py` | LinkedIn (Easy Apply filter) | `data/jobs_easy_apply_raw.json` | 500+ |
| `fetch_reed_jobs.py` | Reed.co.uk (£50k+ filter) | `data/jobs_reed_raw.json` | 500+ |
| `job_scraper.py` | LinkedIn + Reed + career pages (AI-enriched) | `data/jobs_scraped.json` | ~100 |

### Filtering

| Script | Input | Output |
|--------|-------|--------|
| `workflow.py --step 2` | `data/jobs_*_raw.json` | `data/jobs_categorized.json` |

### Applying

| Script | Target | Strategy | Progress File |
|--------|--------|----------|---------------|
| `fresh_easy_apply.py` | LinkedIn Easy Apply | Real-time search + apply | `data/fresh_easy_apply_progress.json` |
| `batch_easy_apply.py` | LinkedIn (pre-scraped) | Fast batch, 8-15s delays | `data/batch_easy_apply_progress.json` |
| `apply_scraped_jobs.py` | External ATS (from LinkedIn) | Follows external links | `data/apply_scraped_progress.json` |
| `apply_all_jobs.py` | All platforms | Multi-agent orchestrator | `data/apply_all_progress.json` |

### Full Pipeline

| Script | Does |
|--------|------|
| `run_full_pipeline.py` | Runs fetch_easy_apply → fetch_reed → apply_all sequentially |
| `workflow.py --from 1` | Runs all 4 steps: fetch → categorize → easy apply → external |

## Session Management

### storage_state.json Lifecycle

```
Login (manual or auto) → cookies saved to data/storage_state.json
  ↓
Scripts load session → browser context with stored cookies
  ↓
Session expires (typically 7-14 days) → scripts detect login page
  ↓
Delete storage_state.json → next run triggers fresh login
```

**Refresh session manually:**
```bash
rm auto_apply/data/storage_state.json
python3 -u fresh_easy_apply.py  # Will prompt for login, then save new session
```

## Progress Tracking

Each apply script maintains a JSON checkpoint file:

```json
{
  "processed": ["url1", "url2", ...]
}
```

Scripts skip URLs already in `processed`. To re-process a job, remove its URL from the progress file.

**Reset all progress** (re-apply to everything):
```bash
echo '{"processed":[]}' > data/apply_all_progress.json
echo '{"processed":[]}' > data/fresh_easy_apply_progress.json
echo '{"processed":[]}' > data/batch_easy_apply_progress.json
echo '{"processed":[]}' > data/apply_scraped_progress.json
```

## Output Files

| File | Format | Contains |
|------|--------|----------|
| `output/apply_all_log.csv` | CSV | timestamp, title, company, url, platform, status, reason |
| `output/fresh_easy_apply_log.csv` | CSV | Same format |
| `output/batch_easy_apply_log.csv` | CSV | Same format |
| `output/scraped_applications_log.csv` | CSV | Same format |
| `output/apply_all_failed.json` | JSON | Jobs that failed with reasons |
| `output/jobs_needing_login.txt` | Text | URLs requiring manual login |
| `output/cover_letters_generated/*.pdf` | PDF | Per-company cover letters |
| `output/screenshots/` | PNG | Debug screenshots (on failure) |

## Anti-Detection Measures

| Measure | Implementation | Config |
|---------|---------------|--------|
| Random delays | 30-120s between applications | `MIN_DELAY_SECONDS`, `MAX_DELAY_SECONDS` |
| Search delays | 10-25s between LinkedIn searches | Hardcoded in fetch scripts |
| Breaks | 45-90s pause every 8-10 queries | Hardcoded |
| Mouse movement | Human-like curves via `humanizer.py` | Always on |
| Reading simulation | Scroll + pause on job descriptions | Always on |
| Rate limiter | Max 5 apps/hour on LinkedIn | `MAX_APPS_PER_HOUR` |
| Session reuse | Stored cookies (no repeated logins) | `storage_state.json` |
| Non-headless | Visible browser (mimics real user) | `headless=False` default |

## Troubleshooting

### Session Expired
**Symptom**: Scripts log "login_required" for every job.
**Fix**:
```bash
rm auto_apply/data/storage_state.json
# Re-run any script — it will prompt for login
```

### Rate Limited by LinkedIn
**Symptom**: "Too many requests" or CAPTCHA page detected.
**Fix**: Wait 30+ minutes. The rate limiter handles this automatically — if a script is running, it will pause and resume.

### Script Hangs (No Output)
**Symptom**: No new log entries for 5+ minutes.
**Fix**:
1. Check latest screenshot: `ls -lt output/screenshots/ | head -5`
2. If CAPTCHA: kill script, wait 30 min, restart
3. If page loaded but stuck: likely a modal detection issue — check browser window

### API Errors (Claude/Bedrock)
**Symptom**: `AuthenticationError` or `ResourceNotFoundException` in output.
**Fix**:
1. Verify `.env` has valid `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
2. Check model IDs match current Bedrock availability
3. If throttled: reduce `MAX_APPS_PER_HOUR` or switch to direct API

### Job Already Applied
**Symptom**: LinkedIn shows "Applied" badge but script tries again.
**Fix**: Script should detect this automatically. If not, the URL is in progress file — no action needed.

### Cover Letter Generation Fails
**Symptom**: `UnicodeEncodeError` or empty PDF.
**Fix**: Check `profile_tools.py` — the FPDF writer sanitizes unicode. If a new character causes issues, add it to the sanitization mapping.

## Adding a New Job Source

1. **Create fetcher**: `auto_apply/fetch_{source}.py`
   - Output to `data/jobs_{source}_raw.json` (same format: id, url, title, company, location, source)
   - Add random delays between page loads
   - Save progress incrementally

2. **Add to pipeline**: Update `run_full_pipeline.py` to call your fetcher

3. **Add agent routing** (if custom ATS):
   - Create `agents/{platform}_agent.py` (see AGENT.md)
   - Add URL detection in `agents/page_classifier.py`
   - Add routing in `apply_all_jobs.py`

4. **Test**: Run fetcher alone first, verify JSON output, then test apply on 2-3 jobs
