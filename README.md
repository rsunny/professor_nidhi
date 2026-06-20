# LinkedIn Job Application Automation

Automated job application system for finance operations roles in London. Uses Playwright to log into LinkedIn, navigate to job URLs, and apply via Easy Apply or external ATS forms.

---

## Architecture

```
auto_apply/
├── main.py                     # Entry point — orchestrates the full apply flow
├── job_scraper.py              # Fetch 300+ jobs from LinkedIn, Reed, company careers
├── config.py                   # Load .env, parse jobs, application data
├── browser.py                  # Playwright browser setup + session persistence
├── linkedin_apply.py           # LinkedIn Easy Apply handler (unlimited modal steps)
├── external_apply.py           # External ATS: Workday, Greenhouse, Lever, Reed, etc.
├── form_filler.py              # Intelligent label→answer matching (regex patterns)
├── humanizer.py                # Random delays (10-30s), scroll, anti-detection
├── cover_letter_manager.py     # Match job → tailored cover letter PDF
├── logger.py                   # CSV tracking + duplicate skip on re-run
├── ai_navigator.py             # (legacy) AI-powered page navigation
├── .env                        # Credentials + config (gitignored)
├── requirements.txt            # Python dependencies
├── data/
│   ├── application_answers.json  # Pre-filled answers for all common form questions
│   ├── jobs.json                 # Original 50 target jobs
│   ├── jobs_new_300.json         # Scraped additional 300 jobs
│   └── jobs_all.json             # Combined list (350 jobs)
└── output/
    ├── storageState.json         # Persistent browser session (login once, reuse)
    ├── applications_log.csv      # Track: job, status, timestamp, notes
    ├── cover_letters/            # 50 pre-generated tailored PDFs
    └── screenshots/              # Failure/review screenshots for debugging
```

---

## How It Works

### Phase 1: Job Discovery (`job_scraper.py`)

Scrapes jobs from 3 sources:
1. **Company career pages** — 23 confirmed UK visa sponsors (Goldman Sachs, JPMorgan, Citi, Barclays, HSBC, UBS, Deutsche Bank, Citadel, Millennium, BlackRock, etc.)
2. **Reed.co.uk** — 10 targeted search queries
3. **LinkedIn** — 29 search queries with 15-30s delays between searches

Jobs are deduplicated, scored for relevance (0-100 based on title keywords + company type), and the top 300 are saved.

### Phase 2: Application (`main.py`)

For each job:
1. Navigate to LinkedIn job URL (10-20s human delay)
2. Simulate reading the job description (15-45s scroll + pause)
3. Detect: Easy Apply vs External link
4. **Easy Apply** → handle multi-step modal (fill fields, upload resume + cover letter, submit)
5. **External** → detect ATS (Workday/Greenhouse/Lever/SmartRecruiters/Reed), fill all pages until submission
6. Log result, take screenshot on failure
7. Wait 30-120 seconds before next application

### Key Features

- **No page depth limits** — follows Next/Continue buttons until submission (safety limit: 30 steps)
- **Human simulation** — 10-30 second delays on LinkedIn, random scrolling, mouse movements
- **Session persistence** — login once (handles 2FA manually), reuse session for days
- **Rate limiting** — max 5 applications/hour
- **Cover letter matching** — 50 tailored PDFs matched by job ID or company name
- **Duplicate detection** — CSV log tracks applied jobs, skips on re-run
- **Review mode** — pauses before submit for manual confirmation (default)
- **Auto mode** — submits without asking (`MODE=auto` in .env)

---

## Setup

### Prerequisites

- Python 3.10+
- Chromium browser (installed by Playwright)

### Installation

```bash
cd /Users/prasanthsunny/Downloads/nidhi/auto_apply

# Create virtual environment (optional)
python3 -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium
```

### Configuration

Edit `.env`:
```env
LINKEDIN_EMAIL=nidhishettyuk23@gmail.com
LINKEDIN_PASSWORD=<actual password here>
MODE=review   # "review" pauses before submit, "auto" submits automatically
MAX_APPS_PER_HOUR=5
MIN_DELAY_SECONDS=30
MAX_DELAY_SECONDS=120
```

---

## Usage

### Step 1: Scrape Jobs (one-time)

```bash
python3 job_scraper.py
```

This opens a browser, visits career pages + LinkedIn + Reed, and saves 300+ relevant jobs to `data/jobs_new_300.json`.

**Runtime:** ~45 minutes (due to anti-detection delays)

### Step 2: Apply to Jobs

```bash
python3 main.py
```

**First run:** Opens browser → logs into LinkedIn → prompts for 2FA if needed → saves session.

**Subsequent runs:** Loads saved session → starts applying immediately.

### Step 3: Monitor Progress

Check `output/applications_log.csv` for real-time results:
```
timestamp, job_id, company, title, url, method, status, notes
```

Check `output/screenshots/` for failure/review screenshots.

---

## Data Files

| File | Purpose |
|------|---------|
| `data/application_answers.json` | All form answers: name, email, phone, visa, salary, experience, etc. |
| `data/jobs.json` | Original 50 curated target jobs |
| `data/jobs_new_300.json` | Scraped additional jobs (sorted by relevance score) |
| `data/jobs_all.json` | Combined list for application |
| `output/cover_letters/*.pdf` | 50 tailored cover letter PDFs |

---

## Form Filling Intelligence

The `form_filler.py` module matches form labels to answers using regex patterns:

| Label Pattern | Answer |
|---------------|--------|
| `first name` | Nidhi |
| `email` | nidhishettyuk23@gmail.com |
| `phone/mobile` | +44 7368 215147 |
| `sponsorship/visa/authorization` | Yes, require Skilled Worker visa |
| `salary/compensation` | 64000 |
| `notice period` | 1 month |
| `years of experience` | 5 |
| `start date` | 1 July 2026 |
| `linkedin` | linkedin.com/in/nidhi-shetty23-1841b7181 |

---

## Anti-Detection Strategy

| Measure | Implementation |
|---------|---------------|
| Realistic user-agent | Chrome 131 on macOS |
| Viewport size | 1366×768 (common laptop) |
| Webdriver flag | Removed via init script |
| Inter-search delay | 15-30 seconds |
| Inter-application delay | 30-120 seconds |
| Page reading simulation | Slow scroll + pause (15-45s) |
| Rate limiting | Max 5 apps/hour |
| Long breaks | 60-120s every 8 LinkedIn searches |
| Session reuse | Login once, persist for days |

---

## Supported ATS Systems

| ATS | Handler | Notes |
|-----|---------|-------|
| LinkedIn Easy Apply | `linkedin_apply.py` | Multi-step modal, unlimited depth |
| Workday | `external_apply.py` | Multi-page, handles navigation buttons |
| Greenhouse | `external_apply.py` | Single page, direct submit |
| Lever | `external_apply.py` | Single page, apply → submit |
| SmartRecruiters | `external_apply.py` | Multi-step with next buttons |
| Reed.co.uk | `external_apply.py` | Apply → form → submit |
| Generic/Unknown | `external_apply.py` | Fills visible fields, clicks Next until Submit |

---

## Relevance Scoring (Job Scraper)

Jobs are scored 0-100 based on:

- **+20 each:** trade support, trade operations, middle office, settlement, prime brokerage, operations analyst, reconciliation
- **+10 each:** fixed income, equity, derivatives, securities, fund operations, collateral, treasury
- **+15:** Tier 1 bank (Goldman, JPM, Citi, Barclays, HSBC, etc.)
- **+12:** Top hedge fund (Millennium, Citadel, Balyasny, Two Sigma, etc.)
- **+10:** Analyst/Associate/Specialist level
- **-20:** Director/Head/Managing level (too senior)
- **-30:** Irrelevant (software engineer, sales, marketing, etc.)

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| LinkedIn session expired | Delete `output/storageState.json`, run `main.py` again |
| 2FA every time | Complete 2FA once, session persists 7-14 days |
| Job page says "no longer accepting" | Logged as "expired", moves to next |
| External form stuck | Screenshot saved, logged as "failed", can retry |
| Rate limited by LinkedIn | Wait 1 hour (auto-handled by rate limiter) |
| Cover letter not found | Falls back to generic `cover_letter_generic.pdf` |

---

## Files That Shouldn't Be Committed

`.gitignore` excludes:
- `.env` (credentials)
- `output/storageState.json` (session cookies)
- `output/screenshots/`
- `output/applications_log.csv`
- `__pycache__/`
