"""Tool-call based form filler — replaces prompt-stuffing with structured tool_use API.

The AI agent calls `lookup_answer` for each form question. The tool resolves the
answer from application_answers.json using pattern matching + common-sense rules.
"""

import json
import os
import re
from pathlib import Path

from config import RESUME_PATH, load_answers

# ---------------------------------------------------------------------------
# Tool Definitions (passed to Claude API as `tools`)
# ---------------------------------------------------------------------------

FORM_TOOLS = [
    {
        "name": "lookup_answer",
        "description": (
            "Look up the correct answer for a form question from the applicant's profile. "
            "Call this for EVERY form field you need to fill — never guess an answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question text or field label exactly as shown on the form",
                },
                "field_type": {
                    "type": "string",
                    "enum": ["text", "number", "select", "radio", "checkbox", "textarea", "upload"],
                    "description": "The type of form field",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Available options for select/radio/checkbox fields",
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
                "index": {"type": "integer", "description": "Element index from the page state"},
                "value": {"type": "string", "description": "The value to enter"},
            },
            "required": ["index", "value"],
        },
    },
    {
        "name": "select_option",
        "description": "Select a dropdown option by its visible text",
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
                "description": {
                    "type": "string",
                    "description": "Brief description of what you're clicking and why",
                },
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
                "index": {"type": "integer", "description": "Element index of the file input/button"},
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
        "description": "Signal that form filling is complete or that you need to stop",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["scanned", "applied", "skipped", "expired", "failed"],
                    "description": "Final status",
                },
                "reason": {"type": "string", "description": "Explanation of why you're done"},
            },
            "required": ["status", "reason"],
        },
    },
]


# ---------------------------------------------------------------------------
# Answer Resolution Logic
# ---------------------------------------------------------------------------

_profile_cache = None


def _load_profile() -> dict:
    """Load and cache the applicant profile."""
    global _profile_cache
    if _profile_cache is None:
        _profile_cache = load_answers()
    return _profile_cache


def invalidate_profile_cache():
    """Force reload of profile on next lookup."""
    global _profile_cache
    _profile_cache = None


def execute_lookup(question: str, field_type: str, options: list[str] | None = None) -> str:
    """
    Resolve the correct answer for a form question from the applicant profile.

    Resolution order:
    1. Pattern matching against question_patterns (regex keys in profile)
    2. Common-sense rules (age, commute, hearing source, etc.)
    3. Direct profile field lookup (keyword matching)
    4. Option best-match (if options provided, pick the closest)
    5. Unknown — flag for review

    Returns a JSON string: {"answer": "...", "confidence": "high|medium|low", "source": "..."}
    """
    profile = _load_profile()
    q = question.lower().strip()

    # 0. Quick-match fields that need special formatting or empty values
    if re.search(r"\bmiddle\s*name\b", q):
        return _result("", "high", "profile_field: no_middle_name")

    # Notice period with unit format
    if re.search(r"notice\s*period.*week|notice.*in\s*weeks", q):
        return _result("4", "high", "profile_field: notice_period_weeks")
    if re.search(r"notice\s*period.*day|notice.*in\s*days", q):
        return _result("30", "high", "profile_field: notice_period_days")

    # Start date with format hint
    if re.search(r"start.*(?:mm/dd|mm-dd)", q) or re.search(r"(?:mm/dd|mm-dd).*(?:start|avail)", q):
        return _result("07/01/2026", "high", "profile_field: start_date_us_format")
    if re.search(r"start.*(?:dd/mm|dd-mm)", q) or re.search(r"(?:dd/mm|dd-mm).*(?:start|avail)", q):
        return _result("01/07/2026", "high", "profile_field: start_date_uk_format")

    # 1. Pattern matching from question_patterns
    # Special handling: "years of experience with/in [SKILL]" — only if "years" is mentioned
    skill_experience_match = re.search(
        r"(?:how many\s+)?years?\s*(?:of\s+)?(?:work\s+)?experience\s*(?:do you have\s+)?(?:with|in)\s+(.+)",
        q, re.IGNORECASE
    )
    if not skill_experience_match:
        # Also catch "Years of [SKILL] experience"
        skill_experience_match = re.search(
            r"years?\s*(?:of\s+)?(.+?)\s+experience",
            q, re.IGNORECASE
        )
    if skill_experience_match:
        skill = skill_experience_match.group(1).strip().rstrip("?").strip()
        answer = _get_skill_years(skill)
        return _result(answer, "high", f"skill_years: {skill}")

    for pattern, answer in profile.get("question_patterns", {}).items():
        if re.search(pattern, q, re.IGNORECASE):
            resolved = answer
            if options:
                resolved = _best_match(answer, options)
            return _result(resolved, "high", f"question_patterns: {pattern}")

    # 2. Common-sense rules
    common_sense = _check_common_sense(q, options, profile, field_type)
    if common_sense:
        return common_sense

    # 3. Direct profile field lookup
    field_answer = _lookup_profile_fields(q, profile)
    if field_answer:
        resolved = field_answer
        if options and field_type in ("select", "radio", "checkbox"):
            resolved = _best_match(field_answer, options)
        return _result(resolved, "high", "profile_field")

    # 4. Motivation / "Why interested?" / cover letter text questions
    if re.search(
        r"why.*interested|interest.*role|interest.*position|why.*apply|"
        r"why.*want.*work|motivation|cover\s*letter|tell.*about.*yourself|"
        r"why.*suitable|what.*attract|why.*this\s*(role|position|company|job)",
        q
    ):
        answer = generate_motivation_answer()
        return _result(answer, "medium", "generated_motivation_answer")

    # 5. If we have options but no match yet, try fuzzy keyword matching
    if options:
        option_answer = _match_options_by_context(q, options, profile)
        if option_answer:
            return _result(option_answer, "medium", "option_context_match")

    # 6. Unknown
    return _result("UNKNOWN", "low", "no_match_found")


def _result(answer: str, confidence: str, source: str) -> str:
    """Format a result dict as JSON string."""
    return json.dumps({"answer": str(answer), "confidence": confidence, "source": source})


def _select_yes(options: list[str] | None) -> str | None:
    """Find a 'Yes' option from available options."""
    if not options:
        return None
    for opt in options:
        if opt.lower().strip() in ("yes", "yes, i do", "yes, i am", "true"):
            return opt
    for opt in options:
        if "yes" in opt.lower():
            return opt
    return None


def _select_no(options: list[str] | None) -> str | None:
    """Find a 'No' option from available options."""
    if not options:
        return None
    for opt in options:
        if opt.lower().strip() in ("no", "no, i don't", "no, i do not", "false"):
            return opt
    for opt in options:
        if "no" in opt.lower():
            return opt
    return None


def _best_match(answer: str, options: list[str]) -> str:
    """Find the best matching option for a given answer."""
    answer_lower = answer.lower().strip()

    # Exact match
    for opt in options:
        if opt.lower().strip() == answer_lower:
            return opt

    # Contains match
    for opt in options:
        if answer_lower in opt.lower() or opt.lower() in answer_lower:
            return opt

    # Word overlap scoring
    answer_words = set(answer_lower.split())
    best_score = 0
    best_opt = options[0] if options else answer
    for opt in options:
        opt_words = set(opt.lower().split())
        overlap = len(answer_words & opt_words)
        if overlap > best_score:
            best_score = overlap
            best_opt = opt

    if best_score > 0:
        return best_opt

    # No good match — return the raw answer (AI will handle it)
    return answer


def _get_skill_years(skill: str) -> str:
    """Return years of experience for a specific skill based on Nidhi's actual background.

    Known skills with real experience:
    - Trade settlement/support/operations: 2.5 years (Morgan Stanley)
    - Financial services (total): 2.5 years
    - Prime Brokerage: 1.5 years
    - Reconciliation: 2 years
    - Python: 1 year (beginner level)
    - Excel/VBA: 5 years
    - Bloomberg: 2 years
    - Data Analysis: 3 years
    - Financial Modeling: 2 years
    - Credit Analysis: 1 year

    Skills with NO/minimal experience: Linux, Oracle, Mortgage, SAP, Hedging specific
    """
    s = skill.lower()

    # Strong skills (2-5 years)
    if re.search(r"excel|vba|spreadsheet", s):
        return "5"
    if re.search(r"python|pandas|automation", s):
        return "1"
    if re.search(r"trade.*(?:support|settlement|operations|processing)|settlement|trade\s*ops", s):
        return "3"
    if re.search(r"financial\s*(?:services|sector|industry)|finance", s):
        return "3"
    if re.search(r"prime\s*brokerage|pb\b", s):
        return "2"
    if re.search(r"reconciliation|recon", s):
        return "2"
    if re.search(r"bloomberg|terminal", s):
        return "2"
    if re.search(r"data\s*analy|analytics", s):
        return "3"
    if re.search(r"financial\s*model|modeling", s):
        return "2"
    if re.search(r"risk|var\b|risk\s*management", s):
        return "2"
    if re.search(r"equity|equities|stock", s):
        return "2"
    if re.search(r"fixed\s*income|bonds", s):
        return "2"
    if re.search(r"credit\s*(?:risk|analysis)", s):
        return "2"
    if re.search(r"commodit", s):
        return "1"
    if re.search(r"compliance|regulatory", s):
        return "1"
    if re.search(r"pricing", s):
        return "2"
    if re.search(r"hedge\s*fund|hedging", s):
        return "1"
    if re.search(r"investment\s*bank", s):
        return "2"

    # Weak/no experience
    if re.search(r"linux|unix|oracle|sap|mortgage|sql\s*server|java\b|c\+\+|\.net", s):
        return "0"

    # Default: if it's finance-adjacent, give 2; otherwise 1
    if re.search(r"financ|bank|invest|trad|market|asset|portfolio|fund", s):
        return "2"
    return "1"


def _check_common_sense(q: str, options: list[str] | None, profile: dict, field_type: str = "text") -> str | None:
    """Handle questions that can be answered with common-sense rules."""

    # Age confirmation (over 16/18)
    if re.search(r"over\s*(16|18)|above\s*(16|18)|at least\s*(16|18)|\bage\b|confirm.*\d+\s*years\s*(?:old|of age)", q):
        yes = _select_yes(options) or "Yes"
        return _result(yes, "high", "common_sense: age_confirmation (applicant is 30)")

    # Comfortable commuting / willing to commute
    if re.search(r"comfortable.*commut|willing.*commut|commut.*location|travel.*office|hybrid|on.?site", q):
        yes = _select_yes(options) or "Yes"
        return _result(yes, "high", "common_sense: willing_to_commute")

    # Right to work / eligible to work
    if re.search(r"right to work|eligible to work|legally.*work|authoriz.*work|authoris.*work|permission to work", q):
        yes = _select_yes(options) or "Yes"
        return _result(yes, "high", "common_sense: right_to_work")

    # British/UK/EU citizen — No (Indian national on Skilled Worker visa)
    if re.search(r"british\s*citizen|uk\s*citizen|eu\s*citizen|eu\s*national|uk\s*national|british\s*national", q):
        no = _select_no(options) or "No"
        return _result(no, "high", "common_sense: not_british_citizen")

    # Require sponsorship / visa sponsorship
    if re.search(r"require.*sponsor|need.*sponsor|sponsor.*require|visa.*sponsor|sponsor.*visa|immigration.*sponsor", q):
        yes = _select_yes(options) or "Yes"
        return _result(yes, "high", "common_sense: requires_sponsorship")

    # How did you hear / find (but NOT if asking for LinkedIn URL/profile)
    if re.search(r"how did you (hear|find|learn)|where did you (hear|find|see|learn)|source|referral", q):
        # Don't trigger if asking for a LinkedIn profile URL
        if not re.search(r"linkedin\s*(profile|url|link|page)|url.*linkedin|profile.*linkedin", q):
            if options:
                answer = _best_match("LinkedIn", options)
            else:
                answer = "LinkedIn"
            return _result(answer, "high", "common_sense: referral_source")

    # Criminal convictions
    if re.search(r"criminal|conviction|offence|offense|unspent", q):
        no = _select_no(options) or "No"
        return _result(no, "high", "common_sense: no_criminal_record")

    # Background check consent
    if re.search(r"background\s*check|consent.*check|agree.*check|dbs", q):
        yes = _select_yes(options) or "Yes"
        return _result(yes, "high", "common_sense: background_check_consent")

    # Currently employed at this company
    if re.search(r"current employee.*this|previously.*work.*here|employed.*this company|state which company", q):
        no = _select_no(options) or "N/A"
        return _result(no, "high", "common_sense: not_current_employee_of_this_company")

    # Willing to relocate
    if re.search(r"willing.*relocat|open.*relocat|relocat", q):
        no = _select_no(options) or "No"
        return _result(no, "high", "common_sense: already_in_london")

    # Disability
    if re.search(r"disabilit|disabled|long.?term.*condition", q):
        if options:
            # Prefer "Prefer not to say" if available, otherwise "No"
            for opt in options:
                if "prefer not" in opt.lower():
                    return _result(opt, "high", "common_sense: disability_prefer_not_to_say")
            no = _select_no(options) or "No"
            return _result(no, "high", "common_sense: no_disability")
        return _result("No", "high", "common_sense: no_disability")

    # Gender
    if re.search(r"\bgender\b|\bsex\b", q) and not re.search(r"sexual", q):
        if options:
            answer = _best_match("Female", options)
        else:
            answer = "Female"
        return _result(answer, "high", "common_sense: gender")

    # Ethnicity
    if re.search(r"ethnic|race|racial", q):
        if options:
            answer = _best_match("Asian - Indian", options)
        else:
            answer = "Asian - Indian"
        return _result(answer, "high", "common_sense: ethnicity")

    # Sexual orientation
    if re.search(r"sexual.*orient|orientation", q):
        if options:
            for opt in options:
                if "prefer not" in opt.lower():
                    return _result(opt, "high", "common_sense: prefer_not_to_say")
            answer = _best_match("Prefer not to say", options)
        else:
            answer = "Prefer not to say"
        return _result(answer, "high", "common_sense: sexual_orientation")

    # Religion
    if re.search(r"religion|faith|belief", q):
        if options:
            for opt in options:
                if "prefer not" in opt.lower():
                    return _result(opt, "high", "common_sense: prefer_not_to_say")
            answer = _best_match("Prefer not to say", options)
        else:
            answer = "Prefer not to say"
        return _result(answer, "high", "common_sense: religion")

    # Veteran / military
    if re.search(r"veteran|military|armed forces|served", q):
        no = _select_no(options) or "No"
        return _result(no, "high", "common_sense: not_veteran")

    # Competing offers
    if re.search(r"other.*offer|competing.*offer|currently.*interviewing", q):
        no = _select_no(options) or "No"
        return _result(no, "medium", "common_sense: no_competing_offers")

    # Graduation year
    if re.search(r"graduat.*year|year.*graduat|when.*graduat|completion.*year", q):
        return _result("2022", "high", "common_sense: graduation_year_msc")

    # Proficiency / skill level questions
    proficiency_match = re.search(r"(?:proficien|skill\s*level|level.*(?:skill|experience)|rate.*(?:skill|ability)).*(?:in|with|for)\s+(.+)", q)
    if not proficiency_match:
        proficiency_match = re.search(r"(?:your|applicant'?s?)\s+(.+?)\s+(?:proficien|skill\s*level|level|ability)", q)
    if proficiency_match:
        skill = proficiency_match.group(1).strip().rstrip("?").strip()
        if re.search(r"python|pandas|programming|coding", skill.lower()):
            if options:
                answer = _best_match("Beginner", options)
            else:
                answer = "Beginner"
            return _result(answer, "high", f"common_sense: python_proficiency_beginner")
        if re.search(r"excel|vba|spreadsheet", skill.lower()):
            if options:
                answer = _best_match("Advanced", options)
            else:
                answer = "Advanced"
            return _result(answer, "high", f"common_sense: excel_proficiency_advanced")
        if re.search(r"bloomberg", skill.lower()):
            if options:
                answer = _best_match("Intermediate", options)
            else:
                answer = "Intermediate"
            return _result(answer, "high", f"common_sense: bloomberg_proficiency_intermediate")
        # Default: Intermediate for finance-related skills
        if options:
            answer = _best_match("Intermediate", options)
        else:
            answer = "Intermediate"
        return _result(answer, "medium", f"common_sense: default_proficiency_{skill}")

    # Skill-specific questions (python, excel, bloomberg, sql) — route based on field/options context
    if re.search(r"\bpython\b", q):
        # If options suggest proficiency levels, answer "Beginner"
        if options and any(x.lower() in ("beginner", "intermediate", "advanced", "expert", "basic") for x in options):
            answer = _best_match("Beginner", options)
            return _result(answer, "high", "common_sense: python_beginner")
        # If yes/no question (do you have python experience?), answer Yes
        if options and any(x.lower() in ("yes", "no") for x in options):
            yes = _select_yes(options) or "Yes"
            return _result(yes, "high", "common_sense: has_python_experience")
        # Text field asking about python → Beginner
        if field_type == "text":
            return _result("Beginner", "high", "common_sense: python_beginner")

    if re.search(r"\bexcel\b|\bvba\b", q):
        if options and any(x.lower() in ("beginner", "intermediate", "advanced", "expert", "basic") for x in options):
            answer = _best_match("Advanced", options)
            return _result(answer, "high", "common_sense: excel_advanced")
        if options and any(x.lower() in ("yes", "no") for x in options):
            yes = _select_yes(options) or "Yes"
            return _result(yes, "high", "common_sense: has_excel_experience")
        if field_type == "text":
            return _result("Advanced - VBA, Power Query, Pivot Tables", "high", "common_sense: excel_advanced")

    if re.search(r"\bbloomberg\b", q):
        if options and any(x.lower() in ("beginner", "intermediate", "advanced", "expert", "basic") for x in options):
            answer = _best_match("Intermediate", options)
            return _result(answer, "high", "common_sense: bloomberg_intermediate")
        if options and any(x.lower() in ("yes", "no") for x in options):
            yes = _select_yes(options) or "Yes"
            return _result(yes, "high", "common_sense: has_bloomberg_experience")
        if field_type == "text":
            return _result("Yes - daily use at Morgan Stanley for trade matching and market data", "high", "common_sense: bloomberg_experience")

    if re.search(r"\bsql\b", q):
        if options and any(x.lower() in ("beginner", "intermediate", "advanced", "expert", "basic") for x in options):
            answer = _best_match("Beginner", options)
            return _result(answer, "high", "common_sense: sql_beginner")
        if options and any(x.lower() in ("yes", "no") for x in options):
            yes = _select_yes(options) or "Yes"
            return _result(yes, "medium", "common_sense: has_sql_knowledge")
        if field_type == "text":
            return _result("Conceptual knowledge of queries and database structure", "high", "common_sense: sql_knowledge")

    # "Do you have experience in/with [SKILL]?" — yes/no skill questions
    skill_exp_match = re.search(r"(?:do you have|have you).+experience.+(?:in|with)\s+(.+)", q)
    if skill_exp_match:
        skill = skill_exp_match.group(1).strip().rstrip("?").strip()
        # Finance-related skills → Yes
        if re.search(r"financ|trad|settlement|reconcil|prime|risk|equity|fixed.income|bloomberg|excel|python|data", skill.lower()):
            yes = _select_yes(options) or "Yes"
            return _result(yes, "high", f"common_sense: has_experience_{skill}")
        # Adjacent skills with some exposure → Yes
        if re.search(r"commodit|credit|compliance|hedg|market|bank|invest|fund|asset", skill.lower()):
            yes = _select_yes(options) or "Yes"
            return _result(yes, "medium", f"common_sense: some_experience_{skill}")
        # Skills with no experience → No
        if re.search(r"linux|oracle|sap|mortgage|sql.server|java\b|\.net|ruby", skill.lower()):
            no = _select_no(options) or "No"
            return _result(no, "high", f"common_sense: no_experience_{skill}")

    return None


def _lookup_profile_fields(q: str, profile: dict) -> str | None:
    """Try to match question to a specific profile field by keywords."""
    personal = profile.get("personal", {})
    employment = profile.get("employment", {})
    salary = profile.get("salary", {})
    work_auth = profile.get("work_authorization", {})
    skills = profile.get("skills", {})
    education = profile.get("education", {})
    additional = profile.get("additional", {})

    # Name fields
    if re.search(r"\bfirst\s*name\b", q):
        return personal.get("first_name")
    if re.search(r"\blast\s*name\b|\bsurname\b|\bfamily\s*name\b", q):
        return personal.get("last_name")
    if re.search(r"\bmiddle\s*name\b", q):
        return ""  # No middle name
    if re.search(r"\bfull\s*name\b", q):
        return personal.get("full_name")
    if re.search(r"\bname\b", q) and "company" not in q and "employer" not in q:
        return personal.get("full_name")

    # Contact
    if re.search(r"\bemail\b", q):
        return personal.get("email")
    if re.search(r"\bphone\b|\bmobile\b|\btelephone\b|\bcell\b|\bcontact number\b", q):
        return personal.get("phone")
    if re.search(r"country\s*code|dialling\s*code|dial\s*code", q):
        return personal.get("phone_country_code", "+44")

    # Location
    if re.search(r"\bpost\s*code\b|\bzip\s*code\b|\bpostal\b", q):
        return personal.get("postcode")
    if re.search(r"\bcity\b|\btown\b", q):
        return personal.get("city")
    if re.search(r"\bcountry\b", q) and "code" not in q:
        return personal.get("country")
    if re.search(r"\baddress\b", q):
        return personal.get("address")
    if re.search(r"\blocation\b", q):
        return personal.get("location", personal.get("city"))

    # LinkedIn
    if re.search(r"\blinkedin\b", q):
        return personal.get("linkedin_url")

    # Website / portfolio
    if re.search(r"\bwebsite\b|\bportfolio\b|\bpersonal.*url\b", q):
        return personal.get("linkedin_url")

    # Nationality
    if re.search(r"\bnationalit\b", q):
        return personal.get("nationality")

    # Date of birth
    if re.search(r"\bdate\s*of\s*birth\b|\bdob\b|\bbirthday\b", q):
        return personal.get("date_of_birth")

    # Salary
    if re.search(r"\bsalary\b|\bcompensation\b|\bpay\b|\bpackage\b|\bexpected.*£\b|\b£.*expect\b", q):
        return salary.get("expected_salary_analyst")
    if re.search(r"\bcurrent\s*salary\b|\bpresent\s*salary\b", q):
        return salary.get("current_salary")

    # Notice period
    if re.search(r"\bnotice\s*period\b|\bnotice\b", q):
        # If asking in weeks, convert
        if "week" in q:
            return "4"
        # If asking in days, convert
        if "day" in q:
            return "30"
        return employment.get("notice_period")

    # Start date / availability
    if re.search(r"\bstart\s*date\b|\bavailab|\bearliest\b|\bwhen.*start\b|\bwhen.*join\b", q):
        # If format specified as mm/dd/yyyy
        if "mm/dd" in q or "mm-dd" in q:
            return "07/01/2026"
        # If format specified as dd/mm/yyyy
        if "dd/mm" in q or "dd-mm" in q:
            return "01/07/2026"
        return employment.get("available_start_date")

    # Currently employed
    if re.search(r"\bcurrently\s*employ\b|\bemployment\s*status\b", q):
        return employment.get("currently_employed")

    # Current employer / company
    if re.search(r"\bcurrent\s*employer\b|\bcurrent\s*company\b|\bcompany\s*name\b|\bemployer\s*name\b", q):
        return employment.get("current_employer")

    # Current job title
    if re.search(r"\bcurrent\s*(job\s*)?title\b|\bjob\s*title\b|\bcurrent\s*role\b", q):
        return employment.get("current_title")

    # Years of experience
    if re.search(r"\byears?\s*(of\s*)?experience\b|\bhow\s*many\s*years\b|\btotal\s*experience\b", q):
        return skills.get("total_years_experience")

    # Education
    if re.search(r"\bhighest.*degree\b|\beducation\b|\bqualification\b|\bdegree\b", q):
        return education.get("highest_degree")
    if re.search(r"\buniversity\b|\binstitution\b|\bcollege\b", q):
        return education.get("university")

    # Visa type
    if re.search(r"\bvisa\s*type\b|\bimmigration\s*status\b|\bwork\s*permit\b", q):
        return work_auth.get("visa_type")

    # Languages
    if re.search(r"\blanguage\b", q):
        return skills.get("languages")

    return None


def _match_options_by_context(q: str, options: list[str], profile: dict) -> str | None:
    """When we have options but no direct match, try to infer from context."""
    personal = profile.get("personal", {})
    work_auth = profile.get("work_authorization", {})

    # Yes/No questions — infer from context
    lower_options = [o.lower().strip() for o in options]
    is_yes_no = all(o in ("yes", "no", "true", "false") for o in lower_options)

    if is_yes_no:
        # Default to Yes for positive permission questions
        if re.search(r"agree|consent|confirm|acknowledge|accept|certif", q):
            return _select_yes(options)
        # Default to No for negative questions
        if re.search(r"restrict|prohibit|prevent|limit", q):
            return _select_no(options)

    # Country selection
    if re.search(r"country|nation\b", q):
        return _best_match("United Kingdom", options)

    # Experience level
    if re.search(r"experience\s*level|seniority|level", q):
        return _best_match("Mid-level", options)

    return None


# ---------------------------------------------------------------------------
# Motivation / "Why interested?" answer generator
# ---------------------------------------------------------------------------

# Job context is injected at runtime via set_current_job()
_current_job: dict = {}


def set_current_job(job: dict):
    """Set the current job context for motivation answers."""
    global _current_job
    _current_job = job


def generate_motivation_answer(job: dict = None) -> str:
    """Generate a tailored 'Why are you interested?' answer for the current job."""
    j = job or _current_job
    title = j.get("title", "this role")
    company = j.get("company", "your company")

    return (
        f"I am keen to join {company} as {title} because it aligns closely with my "
        f"background in trade operations, settlement, and risk management from Morgan Stanley's "
        f"Prime Brokerage division. With 5 years of experience including hands-on work in "
        f"pre-matching, reconciliation, and equity swap lifecycle management across global markets, "
        f"I am confident I can add immediate value. I am proficient in advanced Excel (VBA, Power Query, "
        f"Pivot Tables), Bloomberg Terminal, CTM, and Refinitiv Eikon, with additional experience in "
        f"Python for automation scripting and AI-assisted development tools. My MSc in Investment and "
        f"Risk Finance (Distinction) from the University of Westminster, covering financial modelling, "
        f"portfolio management, and risk management, strengthens my analytical foundation. "
        f"I am particularly drawn to {company}'s reputation in financial services and believe "
        f"my combination of technical expertise and operational experience makes me a strong fit."
    )


# ---------------------------------------------------------------------------
# Cover Letter Resolution
# ---------------------------------------------------------------------------

def get_cover_letter_for_job(job: dict) -> str | None:
    """Find or generate the appropriate cover letter PDF path for a job.

    Checks:
    1. generated_pdfs/ directory for matching company name
    2. auto_apply/output/cover_letters/ for pre-generated ones
    3. Falls back to generic cover letter
    """
    from config import GENERIC_COVER_LETTER, BASE_DIR

    company = job.get("company", "").replace("/", "-").replace(" ", "_")
    job_id = job.get("id", 0)

    # Check generated_pdfs/ directory
    generated_dir = BASE_DIR.parent / "generated_pdfs"
    if generated_dir.exists():
        # Try exact company name match
        for pdf in generated_dir.glob("cover_letter_*"):
            if company and company.lower().replace("_", "") in pdf.stem.lower().replace("_", ""):
                return str(pdf)

    # Check auto_apply/output/cover_letters/
    output_cl_dir = BASE_DIR / "output" / "cover_letters"
    if output_cl_dir.exists():
        for pdf in output_cl_dir.glob("cover_letter_*"):
            if company and company.lower().replace("_", "") in pdf.stem.lower().replace("_", ""):
                return str(pdf)

    # Fall back to generic
    if GENERIC_COVER_LETTER.exists():
        return str(GENERIC_COVER_LETTER)

    return None


# ---------------------------------------------------------------------------
# System Prompt for Tool-Based Form Filling
# ---------------------------------------------------------------------------

def build_tool_system_prompt(job: dict, resume_path: str, cover_letter_path: str = "") -> str:
    """Build a minimal system prompt for tool-based form filling.

    Unlike the old approach, answers are NOT in the prompt — the AI calls
    lookup_answer for each field.
    """
    return f"""You are filling a LinkedIn Easy Apply form. The form is already open in a modal dialog.

JOB: {job.get('title', '')} at {job.get('company', '')}

You have these tools:
- lookup_answer: Call this for EVERY question/field you encounter. It returns the correct answer from the applicant's profile.
- fill_field: Fill a text/number input field with a value.
- select_option: Select a dropdown option by visible text.
- click_element: Click buttons, radio buttons, checkboxes, or links.
- upload_file: Upload resume or cover letter.
- done: Signal that form filling is complete.

WORKFLOW:
1. Look at the form elements presented to you.
2. For each EMPTY field that needs filling:
   a. Call lookup_answer with the question/label text and field type
   b. Use the returned answer to fill_field or select_option
3. For radio buttons (Yes/No): call lookup_answer first, then click_element on the correct label.
4. When you see a file upload section: use upload_file with file_type="resume".
5. Click "Next" / "Continue" / "Review" buttons to advance through form pages.
6. When you see "Submit application" button: call done(status="scanned", reason="reached submit page").

RULES:
- Call lookup_answer BEFORE filling any field — never guess answers.
- Skip fields that are already filled correctly (non-empty with correct value).
- For radio/checkbox: click the LABEL text, not the input element itself.
- You may make multiple tool calls in a single turn if they are independent.
- If lookup_answer returns confidence "low" or answer "UNKNOWN", still fill with best guess and note it.
- After clicking Next/Continue, wait for the next page of elements.

RESUME: {resume_path}
COVER LETTER: {cover_letter_path or 'not available'}
"""


def build_tool_submit_prompt(job: dict, resume_path: str, cover_letter_path: str = "") -> str:
    """Same as build_tool_system_prompt but instructs to SUBMIT the form."""
    base = build_tool_system_prompt(job, resume_path, cover_letter_path)
    # Replace the scanned instruction with submit
    return base.replace(
        'When you see "Submit application" button: call done(status="scanned", reason="reached submit page").',
        'When you see "Submit application" button: click_element on it to submit, then call done(status="applied", reason="submitted application").'
    )
