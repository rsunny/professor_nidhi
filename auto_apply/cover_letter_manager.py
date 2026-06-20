"""Cover letter matching — find the right cover letter for each job."""

import re
import tempfile
from pathlib import Path

from config import COVER_LETTER_DIR, GENERIC_COVER_LETTER


def parse_cover_letters(md_path: Path = None) -> dict[str, str]:
    """Parse cover_letters.md into a dict keyed by heading (company/role info)."""
    if md_path is None:
        md_path = COVER_LETTER_DIR / "cover_letters.md"

    if not md_path.exists():
        return {}

    with open(md_path) as f:
        content = f.read()

    letters = {}
    # Split by ## headings
    sections = re.split(r"^## ", content, flags=re.MULTILINE)

    for section in sections[1:]:  # Skip preamble before first ##
        lines = section.strip().split("\n")
        heading = lines[0].strip()
        # Body is everything after the heading, stripped of leading/trailing whitespace
        body_lines = []
        in_body = False
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if line.strip().startswith("Dear") or line.strip().startswith("Hi "):
                in_body = True
            if in_body:
                body_lines.append(line)

        if body_lines:
            letters[heading] = "\n".join(body_lines).strip()

    return letters


def match_cover_letter(
    job: dict, letters: dict[str, str]
) -> tuple[str, str]:
    """Match a job to its cover letter. Returns (letter_text, match_type)."""
    company = job.get("company", "").lower()
    title = job.get("title", "").lower()
    job_id = job.get("id", 0)

    # Try matching by job number in heading (e.g., "#1 — Trade Support Analyst | JP Morgan")
    for heading, text in letters.items():
        # Extract number from heading
        num_match = re.search(r"#(\d+)", heading)
        if num_match and int(num_match.group(1)) == job_id:
            return text, "exact_id"

    # Try matching by company name
    for heading, text in letters.items():
        heading_lower = heading.lower()
        if company and len(company) > 3:
            # Check if company name appears in heading
            company_words = [w for w in company.split() if len(w) > 3]
            if any(word.lower() in heading_lower for word in company_words):
                return text, "company_match"

    # Try matching by job title keywords
    for heading, text in letters.items():
        heading_lower = heading.lower()
        title_words = [w for w in title.split() if len(w) > 4]
        matches = sum(1 for w in title_words if w.lower() in heading_lower)
        if matches >= 2:
            return text, "title_match"

    return "", "no_match"


def get_cover_letter_pdf(job: dict, letters: dict[str, str]) -> Path:
    """Get the PDF cover letter for a job. Check pre-generated PDFs first."""
    from config import BASE_DIR

    job_id = job.get("id", 0)
    company = job.get("company", "Unknown").replace("/", "-").replace(" ", "_")

    # Check pre-generated PDFs in output/cover_letters/ first
    pre_generated_dir = BASE_DIR / "output" / "cover_letters"
    if pre_generated_dir.exists():
        expected_name = f"cover_letter_{job_id}_{company}.pdf"
        expected_path = pre_generated_dir / expected_name
        if expected_path.exists():
            return expected_path
        # Try fuzzy match on job_id prefix
        for pdf in pre_generated_dir.glob(f"cover_letter_{job_id}_*.pdf"):
            return pdf

    # Try matching from cover_letters.md text
    text, match_type = match_cover_letter(job, letters)

    if not text or match_type == "no_match":
        # Use generic cover letter
        if GENERIC_COVER_LETTER.exists():
            return GENERIC_COVER_LETTER
        return None

    # Generate PDF from the matched text
    return generate_cover_letter_pdf(text, job)


def generate_cover_letter_pdf(text: str, job: dict) -> Path:
    """Convert cover letter text to a PDF file."""
    try:
        from weasyprint import HTML

        company = job.get("company", "Unknown").replace("/", "-").replace(" ", "_")
        job_id = job.get("id", 0)

        # Create output directory for generated PDFs
        pdf_dir = Path(COVER_LETTER_DIR) / "generated_pdfs"
        pdf_dir.mkdir(exist_ok=True)
        pdf_path = pdf_dir / f"cover_letter_{job_id}_{company}.pdf"

        # If already generated, return existing
        if pdf_path.exists():
            return pdf_path

        # Convert markdown-ish text to HTML
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{
                    font-family: 'Calibri', 'Helvetica Neue', Arial, sans-serif;
                    font-size: 11pt;
                    line-height: 1.5;
                    margin: 2.5cm;
                    color: #333;
                }}
                p {{ margin-bottom: 12pt; }}
            </style>
        </head>
        <body>
            {"".join(f"<p>{para}</p>" for para in text.split("\n\n") if para.strip())}
        </body>
        </html>
        """

        HTML(string=html_content).write_pdf(str(pdf_path))
        return pdf_path

    except ImportError:
        # WeasyPrint not available — fall back to generic
        print("  ⚠️  WeasyPrint not installed; using generic cover letter")
        if GENERIC_COVER_LETTER.exists():
            return GENERIC_COVER_LETTER
        return None
    except Exception as e:
        print(f"  ⚠️  PDF generation failed: {e}; using generic")
        if GENERIC_COVER_LETTER.exists():
            return GENERIC_COVER_LETTER
        return None
