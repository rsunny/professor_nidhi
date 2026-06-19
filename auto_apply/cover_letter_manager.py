"""Cover letter matching — parse cover_letters.md, match to jobs, generate PDFs."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Dict, Optional
from config import COVER_LETTERS_MD_PATH, COVER_LETTER_GENERIC_PATH


def parse_cover_letters(md_path: str = None) -> Dict[str, str]:
    """Parse cover_letters.md into a dict keyed by company/role identifier.

    Returns: { "jp morgan": "Dear Hiring Manager...", "goldman sachs_treasury": "Dear..." }
    """
    path = md_path or COVER_LETTERS_MD_PATH
    with open(path, "r") as f:
        content = f.read()

    letters = {}
    # Split by ## headings
    sections = re.split(r"^## ", content, flags=re.MULTILINE)

    for section in sections[1:]:  # Skip content before first ##
        lines = section.strip().split("\n")
        heading = lines[0].strip()

        # Extract body (skip heading line and any blank lines / dashes)
        body_lines = []
        in_body = False
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if line.strip().startswith("Dear ") or line.strip().startswith("Hi "):
                in_body = True
            if in_body:
                body_lines.append(line)

        body = "\n".join(body_lines).strip()
        if not body:
            continue

        # Create multiple keys for matching
        # Key by number: "#1", "#2", etc.
        num_match = re.search(r"#(\d+)", heading)
        if num_match:
            letters[f"#{num_match.group(1)}"] = body

        # Key by company name (lowercase, extracted from heading)
        # Heading format: "#1 — Trade Support Analyst | JP Morgan"
        company_match = re.search(r"\|\s*(.+?)$", heading)
        if company_match:
            company = company_match.group(1).strip().lower()
            letters[company] = body

        # Also key by full heading for fuzzy matching
        letters[heading.lower()] = body

    return letters


# Cache parsed letters
_letters_cache: Dict[str, str] = {}


def get_letters() -> Dict[str, str]:
    """Get cached parsed cover letters."""
    global _letters_cache
    if not _letters_cache:
        _letters_cache = parse_cover_letters()
    return _letters_cache


def find_cover_letter_for_job(job: dict) -> Optional[str]:
    """Find the best matching cover letter for a job.

    Tries matching by:
    1. Job ID (e.g., "#1")
    2. Company name (fuzzy)
    3. Returns None if no specific match (will use generic)
    """
    letters = get_letters()
    job_id = job.get("id")
    company = job.get("company", "").lower()
    title = job.get("title", "").lower()

    # 1. Try exact ID match
    key = f"#{job_id}"
    if key in letters:
        return letters[key]

    # 2. Try company name match
    for letter_key, letter_body in letters.items():
        if not letter_key.startswith("#"):
            # Check if company name is contained in the key
            if company and company in letter_key:
                return letter_body
            # Check if key company is in the job company
            if letter_key in company:
                return letter_body

    # 3. Try partial company matching (for recruiters like "Mondrian Alpha")
    company_words = company.split()
    for letter_key, letter_body in letters.items():
        if not letter_key.startswith("#"):
            for word in company_words:
                if len(word) > 3 and word in letter_key:
                    return letter_body

    return None


def get_cover_letter_pdf_path(job: dict) -> str:
    """Get the path to the appropriate cover letter PDF for a job.

    If a specific cover letter exists, generate a PDF from it.
    Otherwise, return the generic cover letter PDF path.
    """
    letter_text = find_cover_letter_for_job(job)

    if letter_text:
        # Generate a PDF from the text
        return generate_cover_letter_pdf(letter_text, job)
    else:
        # Use generic
        return COVER_LETTER_GENERIC_PATH


def generate_cover_letter_pdf(text: str, job: dict) -> str:
    """Generate a professional PDF from cover letter text using WeasyPrint."""
    try:
        from weasyprint import HTML

        company = job.get("company", "Unknown").replace(" ", "_").replace("/", "_")
        job_id = job.get("id", 0)
        output_dir = Path(__file__).parent / "output" / "cover_letters"
        output_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = output_dir / f"cover_letter_{job_id}_{company}.pdf"

        # If already generated, reuse
        if pdf_path.exists():
            return str(pdf_path)

        # Convert to HTML with professional styling
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{
                    font-family: 'Georgia', serif;
                    font-size: 11pt;
                    line-height: 1.6;
                    margin: 2.5cm 2.5cm 2.5cm 2.5cm;
                    color: #222;
                }}
                .header {{
                    margin-bottom: 20px;
                }}
                .name {{
                    font-size: 14pt;
                    font-weight: bold;
                    margin-bottom: 4px;
                }}
                .contact {{
                    font-size: 9pt;
                    color: #555;
                }}
                .date {{
                    margin-top: 20px;
                    margin-bottom: 20px;
                }}
                p {{
                    margin-bottom: 12px;
                    text-align: justify;
                }}
            </style>
        </head>
        <body>
            <div class="header">
                <div class="name">Nidhi Shetty</div>
                <div class="contact">nidhishettyuk23@gmail.com | +44 7368 215147 | London, UK</div>
                <div class="contact">linkedin.com/in/nidhi-shetty23-1841b7181</div>
            </div>
            {"".join(f"<p>{line}</p>" if line.strip() else "" for line in text.split(chr(10)) if line.strip())}
        </body>
        </html>
        """

        HTML(string=html_content).write_pdf(str(pdf_path))
        print(f"[cover-letter] Generated PDF: {pdf_path.name}")
        return str(pdf_path)

    except ImportError:
        print("[cover-letter] WeasyPrint not installed — falling back to generic PDF")
        return COVER_LETTER_GENERIC_PATH
    except Exception as e:
        print(f"[cover-letter] PDF generation failed: {e} — using generic")
        return COVER_LETTER_GENERIC_PATH
