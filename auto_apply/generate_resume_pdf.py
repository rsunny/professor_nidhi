"""One-time script to regenerate Nidhi's resume PDF — 2-page version."""

from weasyprint import HTML
from pathlib import Path

OUTPUT_PATH = Path("/Users/prasanthsunny/Downloads/nidhi/Nidhi_Shetty_Resume.pdf")

html_content = """
<!DOCTYPE html>
<html>
<head>
<style>
    @page {
        size: A4;
        margin: 1.5cm 1.8cm 1.5cm 1.8cm;
    }
    body {
        font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
        font-size: 9.5pt;
        line-height: 1.45;
        color: #1a1a1a;
    }
    h1 {
        font-size: 20pt;
        font-weight: 700;
        margin: 0 0 3px 0;
        letter-spacing: 0.5px;
    }
    .contact {
        font-size: 9.5pt;
        color: #444;
        margin-bottom: 12px;
    }
    h2 {
        font-size: 10.5pt;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        border-bottom: 1.5px solid #1a1a1a;
        padding-bottom: 2px;
        margin-top: 14px;
        margin-bottom: 6px;
    }
    h3 {
        font-size: 10pt;
        font-weight: 700;
        margin: 0 0 1px 0;
    }
    .role-meta {
        font-size: 9pt;
        color: #555;
        margin-bottom: 4px;
    }
    ul {
        margin: 3px 0 10px 0;
        padding-left: 16px;
    }
    li {
        margin-bottom: 3px;
    }
    .profile {
        margin-bottom: 10px;
    }
    .achievements {
        margin-bottom: 6px;
    }
    .skills-table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 4px;
    }
    .skills-table td {
        padding: 3px 6px;
        vertical-align: top;
        font-size: 9.5pt;
    }
    .skills-table td:first-child {
        font-weight: 600;
        width: 145px;
        white-space: nowrap;
    }
    .certs {
        columns: 3;
        column-gap: 20px;
        font-size: 9pt;
        margin-top: 4px;
    }
    .certs div {
        break-inside: avoid;
        margin-bottom: 2px;
    }
    .interests {
        font-size: 9pt;
        margin-top: 4px;
    }
</style>
</head>
<body>

<h1>NIDHI SHETTY</h1>
<div class="contact">+44 7368 215147 | nidhishettyuk23@gmail.com | linkedin.com/in/nidhi-shetty23</div>

<h2>Profile</h2>
<div class="profile">
Operations and finance professional with hands-on experience in prime brokerage settlement, fixed income and equity trade lifecycle, and risk management at Morgan Stanley. Combines quantitative analysis skills with AI-assisted Python automation and Bloomberg Terminal proficiency to improve operational efficiency across global markets. Experienced in leveraging AI tools (Claude, ChatGPT) for rapid prototyping, data analysis scripting, and process automation. Looking to leverage this experience in an investment banking or institutional finance environment where trade operations expertise and analytical capabilities drive measurable impact.
</div>

<h2>Key Achievements</h2>
<ul class="achievements">
    <li>Improved Straight-Through Processing (STP) rates by 10% at Morgan Stanley by building Python/Excel reconciliation tools that auto-flagged mismatches before settlement deadlines.</li>
    <li>Managed pre-matching and settlement of fixed income and equity trades across US, EMEA, and APAC markets, processing $10M+ in daily volume for institutional clients.</li>
    <li>Developed AI-assisted automation workflows (Claude, ChatGPT) that reduced manual reporting time by 80% in current role.</li>
</ul>

<h2>Experience</h2>

<h3>Advertising Account Manager</h3>
<div class="role-meta">Here and Now 365, London | May 2024 – Present</div>
<ul>
    <li>Built Python scripts using AI-assisted development (Claude, ChatGPT) to automate campaign metric extraction from platform APIs, reducing manual reporting time from 4 hours to 45 minutes per campaign cycle.</li>
    <li>Leveraged AI tools to rapidly prototype data pipelines and automate repetitive workflows, including client report generation and cross-platform data reconciliation.</li>
    <li>Delivered post-campaign analysis using statistical modelling to isolate channel-level ROI, directly informing budget reallocation that improved next-quarter campaign performance by 15%.</li>
    <li>Led client presentations translating complex data into strategic recommendations, growing account revenue through upselling by 20%.</li>
    <li>Coordinated cross-functional delivery across creative and media teams, managing timelines and dependencies for 8+ concurrent campaigns.</li>
</ul>

<h3>Operations Analyst, Prime Brokerage</h3>
<div class="role-meta">Morgan Stanley, Glasgow | Jan 2023 – Apr 2024</div>
<ul>
    <li>Managed pre-matching and settlement of fixed income and equity trades across US, EMEA, and APAC markets for hedge fund and institutional clients, processing $10M+ in daily trade volume.</li>
    <li>Executed daily start-of-day and end-of-day risk controls, managing equity swap lifecycle events including corporate actions and expiries for prime brokerage clients.</li>
    <li>Improved Straight-Through Processing (STP) rates by 10% by identifying recurring break patterns and building Excel/Python reconciliation tools to auto-flag mismatches before settlement deadlines.</li>
    <li>Resolved complex settlement failures involving tri-party repos and cross-border custody transfers, reducing average resolution time from 3 days to same-day for priority clients.</li>
    <li>Identified and escalated potential market and regulatory risks by monitoring post-trade exceptions in TM and SafeGUI, reducing incident occurrences by 15% quarter-over-quarter.</li>
    <li>Performed trade matching via CTM and collaborated with account managers, compliance, and Front/Middle/Back Office teams across multiple time zones to address margin call discrepancies and ensure adherence to CSDR and T+1 settlement requirements.</li>
</ul>

<h3>Investment Banking & Asset Management Internship</h3>
<div class="role-meta">Bright Network, London | Jun 2021</div>
<ul>
    <li>Conducted equity research and company valuations using DCF and comparable analysis, presenting investment recommendations to senior analysts.</li>
    <li>Built financial models in Excel and Python to evaluate M&A scenarios in the infrastructure and real assets space.</li>
    <li>Gained exposure to MIRA's global infrastructure portfolio and asset lifecycle management including acquisition, diversification, and disposal strategies.</li>
</ul>

<h3>Account Assistant</h3>
<div class="role-meta">Bharath Shetty Tax Consultant, Mumbai | Sep 2018 – Aug 2019</div>
<ul>
    <li>Prepared and analysed financial statements for 30+ corporate and individual clients, using Tally ERP and Excel to identify discrepancies and recommend corrective actions.</li>
    <li>Managed end-to-end tax filing (corporate, individual, GST) collaborating with senior consultants to optimise strategies, achieving average client savings of 12–15% on effective tax burden.</li>
    <li>Automated recurring data entry tasks using Python scripts and Excel macros, reducing manual processing time by 25%.</li>
</ul>

<h2>Education</h2>

<h3>MSc Investment and Risk Finance — Distinction</h3>
<div class="role-meta">University of Westminster, London | Jan 2021 – Jun 2022</div>
<ul>
    <li>Modules: Modern Portfolio Management, Financial Modelling, International Risk Management, Forecasting & Market Risk Modelling, Financial Markets & Institutions</li>
    <li>Dissertation: Do Acquirers Gain Value in Entertainment & Media Sector Takeover Transactions?</li>
</ul>

<h3>Master of Commerce — Merit</h3>
<div class="role-meta">University of Mumbai | Aug 2017 – Jan 2019</div>
<ul>
    <li>Modules: Advanced Financial Accounting, Financial Management, Economics of Global Trade & Finance</li>
</ul>

<h3>Bachelor of Commerce — GPA: 5.8/7</h3>
<div class="role-meta">University of Mumbai | Jun 2013 – Jan 2017</div>

<h2>Technical Skills</h2>
<table class="skills-table">
    <tr>
        <td>Platforms & Tools:</td>
        <td>Bloomberg Terminal, Refinitiv Eikon, SafeGUI, TM (Trade Management), CTM, MS Office Suite</td>
    </tr>
    <tr>
        <td>Programming & Data:</td>
        <td>Python (pandas, numpy, automation scripts), AI-assisted development (Claude, ChatGPT, Copilot), SQL (basic), Advanced Excel (VBA, Power Query, Pivot Tables)</td>
    </tr>
    <tr>
        <td>Domain Expertise:</td>
        <td>Trade settlement (T+1/T+2), Prime brokerage ops, Fixed income & equity markets, Equity swaps & derivatives, Reconciliation, Risk assessment, CSDR compliance, STP optimisation, Financial modelling, Process automation & AI workflow integration</td>
    </tr>
</table>

<h2>Certifications</h2>
<div class="certs">
    <div>CITI Virtual Experience Program</div>
    <div>Complete Financial Analyst Course</div>
    <div>Economics for Capital Markets</div>
    <div>Company Valuation & Financial Modeling</div>
    <div>Introduction to Derivatives</div>
    <div>Introduction to Corporate Finance</div>
    <div>Bloomberg Essentials</div>
    <div>Fundamentals of Credit</div>
    <div>Reading Financial Statements</div>
</div>

<h2>Interests</h2>
<div class="interests">
District-level Dodgeball player · Taekwondo Black Belt · Volunteering · Financial blogs & market commentary
</div>

</body>
</html>
"""

if __name__ == "__main__":
    HTML(string=html_content).write_pdf(str(OUTPUT_PATH))
    # Check page count
    from weasyprint import HTML as WH
    doc = WH(string=html_content).render()
    pages = len(doc.pages)
    print(f"✅ Resume PDF generated: {OUTPUT_PATH}")
    print(f"   Pages: {pages}")
