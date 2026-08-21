"""Generate a realistic-looking sample document containing PII, for the demo."""
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
import sys

styles = getSampleStyleSheet()
styles.add(ParagraphStyle("Small", parent=styles["Normal"], fontSize=9, leading=13))

out = sys.argv[1] if len(sys.argv) > 1 else "samples/sample_pii.pdf"
doc = SimpleDocTemplate(out, pagesize=letter, topMargin=0.8 * inch)
S = []

S.append(Paragraph("Northwind Logistics — Employee Onboarding Record", styles["Title"]))
S.append(Paragraph("CONFIDENTIAL — Contains Personal Information", styles["Italic"]))
S.append(Spacer(1, 16))

S.append(Paragraph("Section 1 — Personal Details", styles["Heading2"]))
rows = [
    ["Full Name", "Jane A. Doe"],
    ["Date of Birth", "14/03/1989"],
    ["Email", "jane.doe@northwind-logistics.com"],
    ["Mobile", "(415) 555-0132"],
    ["Home Address", "27 Collins Street, Melbourne VIC 3000"],
    ["US Social Security No.", "536-90-4399"],
    ["AU Tax File Number", "123 456 782"],
    ["AU Medicare No.", "2123 45670 1"],
]
t = Table(rows, colWidths=[2.2 * inch, 3.8 * inch])
t.setStyle(TableStyle([
    ("FONTSIZE", (0, 0), (-1, -1), 9),
    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
    ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("ROWBACKGROUNDS", (1, 0), (1, -1), [colors.white, colors.white]),
]))
S.append(t)
S.append(Spacer(1, 16))

S.append(Paragraph("Section 2 — Payroll", styles["Heading2"]))
S.append(Paragraph(
    "Salary is deposited to corporate card ending in the number on file: "
    "<b>4111 1111 1111 1111</b>. For payroll questions email "
    "payroll@northwind-logistics.com or call +61 3 9000 1234. "
    "Employee ID: EMP-00921. Manager ID: EMP-00455.",
    styles["Small"]))
S.append(Spacer(1, 12))

S.append(Paragraph("Section 3 — Notes", styles["Heading2"]))
S.append(Paragraph(
    "Background check returned clean. Candidate previously contacted via "
    "personal email jdoe.personal@gmail.com and phone 0412 345 678. "
    "An expired test card 1234 5678 9012 3456 appears in legacy notes and "
    "should be disregarded by any validating system.",
    styles["Small"]))

doc.build(S)
print(f"wrote {out}")
