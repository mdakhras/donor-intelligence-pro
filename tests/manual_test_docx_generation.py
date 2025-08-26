import os
from utils.document_generator import generate_docx

# Sample test report
sample_report = """
Donor Intelligence Briefing Report

Overview:
The Gates Foundation focuses heavily on health systems strengthening in Sub-Saharan Africa, particularly around primary care delivery.

Key Players:
- Melinda French Gates
- Global Development Program
- Local ministries of health

Thematic Priorities:
- Primary healthcare innovation
- Vaccine distribution
- Digital health systems

Geographic Priorities:
- Nigeria, Kenya, Ethiopia

History of Funding / Programming:
- $1B committed to GAVI
- $250M toward COVID-19 response

Opportunities:
- New call for proposals: Digital Health Africa 2025 (Due: Oct 1)
- Strategic partnership with WHO announced June 2025

Strategic Angle:
Align proposal with digital transformation of primary healthcare. Emphasize regional partnerships and data-driven monitoring.

Talking Points:
- Mention Gates' investment in localized solutions
- Highlight successful pilots in Kenya/Ethiopia
"""

# Path to save the Word document
output_path = "tests/generated_sample_donor_report.docx"

# Generate the document
generate_docx(sample_report, output_path)

# Confirm creation
if os.path.exists(output_path):
    print(f"✅ Test passed! File created at: {output_path}")
else:
    print("❌ Test failed. File was not created.")
