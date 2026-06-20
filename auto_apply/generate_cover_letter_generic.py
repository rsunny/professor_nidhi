"""Regenerate the generic cover letter PDF to match updated resume."""

from weasyprint import HTML
from pathlib import Path

OUTPUT_PATH = Path("/Users/prasanthsunny/Downloads/nidhi/cover_letter_generic.pdf")

html_content = """
<!DOCTYPE html>
<html>
<head>
<style>
    @page {
        size: A4;
        margin: 2.5cm 2.5cm 2.5cm 2.5cm;
    }
    body {
        font-family: 'Georgia', serif;
        font-size: 11pt;
        line-height: 1.6;
        color: #222;
    }
    .header {
        margin-bottom: 20px;
    }
    .name {
        font-size: 14pt;
        font-weight: bold;
        margin-bottom: 4px;
    }
    .contact {
        font-size: 9pt;
        color: #555;
    }
    .date {
        margin-top: 20px;
        margin-bottom: 20px;
    }
    p {
        margin-bottom: 12px;
        text-align: justify;
    }
    ul {
        margin: 8px 0 12px 0;
        padding-left: 20px;
    }
    li {
        margin-bottom: 6px;
    }
    .section-head {
        font-weight: bold;
        margin-top: 16px;
        margin-bottom: 6px;
    }
</style>
</head>
<body>

<div class="header">
    <div class="name">Nidhi Shetty</div>
    <div class="contact">+44 7368 215147</div>
    <div class="contact">nidhishettyuk23@gmail.com</div>
    <div class="contact">linkedin.com/in/nidhi-shetty23</div>
</div>

<div class="date">June 2026</div>

<p>Dear Hiring Manager,</p>

<p>I am writing to apply for this position. With hands-on experience in prime brokerage settlement, trade lifecycle management, and risk operations at Morgan Stanley, combined with AI-assisted automation expertise, I am confident in my ability to contribute to your team from day one.</p>

<p class="section-head">What I bring:</p>

<p>At Morgan Stanley's Prime Brokerage in Glasgow, I managed pre-matching and settlement of fixed income and equity trades across US, EMEA, and APAC markets for hedge fund and institutional clients. My key contributions included:</p>

<ul>
    <li>Improving Straight-Through Processing (STP) rates by 10% by building Python and Excel reconciliation tools that auto-flagged mismatches before settlement deadlines.</li>
    <li>Executing daily risk controls and managing equity swap lifecycle events including corporate actions and expiries, performing trade matching via CTM across multiple time zones.</li>
    <li>Resolving complex settlement failures involving tri-party repos and cross-border custody transfers, reducing average resolution time from 3 days to same-day for priority clients.</li>
</ul>

<p>In my current role, I have developed AI-assisted automation workflows using Claude and ChatGPT to rapidly prototype data pipelines, automate reporting, and build reconciliation tools — reducing manual processing time by 80%.</p>

<p class="section-head">Technical skills:</p>

<p>I am proficient in Python (pandas, numpy, automation scripting), AI-assisted development (Claude, ChatGPT, Copilot), advanced Excel (VBA, Power Query, Pivot Tables), Bloomberg Terminal, CTM, and Refinitiv Eikon. I hold an MSc in Investment and Risk Finance (Distinction) from the University of Westminster, with coursework in financial modelling, portfolio management, and risk management.</p>

<p>I would welcome the opportunity to discuss how my experience aligns with your team's needs. I am available for a conversation at your convenience.</p>

<p>Kind regards,<br>Nidhi Shetty</p>

</body>
</html>
"""

if __name__ == "__main__":
    HTML(string=html_content).write_pdf(str(OUTPUT_PATH))
    print(f"✅ Generic cover letter PDF generated: {OUTPUT_PATH}")
