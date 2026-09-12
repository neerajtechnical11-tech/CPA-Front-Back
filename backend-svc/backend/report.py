"""Accenture-branded .xlsx compliance report (openpyxl)."""
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

PURPLE = "FFA100FF"
GREY = "FFF2F2F2"
WHITE = "FFFFFFFF"
BORDER = "FFCCCCCC"
RED = "FFC00000"
ORANGE = "FFE8830C"
GREEN = "FF1F9D55"

_thin = Side(style="thin", color=BORDER)
_box = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
_risk_fill = {"High": RED, "Medium": ORANGE, "Low": "FFC9A200", "None": GREEN}


def _cell(ws, r, c, val, *, bold=False, fill=None, color="FF000000", wrap=False, size=10):
    cell = ws.cell(row=r, column=c, value=val)
    cell.font = Font(name="Arial", size=size, bold=bold, color=color)
    cell.alignment = Alignment(vertical="center", wrap_text=wrap)
    cell.border = _box
    if fill:
        cell.fill = PatternFill("solid", fgColor=fill)
    return cell


def write_report(result: dict, out_path: str, doc_name: str = "Uploaded document"):
    wb = Workbook()
    ws = wb.active
    ws.title = "Compliance Findings"

    # Title row
    ws.merge_cells("A1:G1")
    t = _cell(ws, 1, 1, f"Compliance Gap Analysis â€” {doc_name}", bold=True, size=15)
    t.fill = PatternFill("solid", fgColor=WHITE)
    ws.cell(1, 1).border = Border(bottom=Side(style="medium", color=PURPLE))

    s = result["summary"]
    _cell(ws, 2, 1, f"Compliance score: {result['compliance_score']}%   "
                    f"(Covered {s['covered']} / Partial {s['partial']} / Gap {s['gap']} of {s['total']})",
          bold=True, color="FF505050")

    headers = ["Framework", "Control", "Title", "State", "Risk", "Best sim.", "Gap narrative"]
    for c, h in enumerate(headers, 1):
        _cell(ws, 4, c, h, bold=True, fill=PURPLE, color=WHITE, size=11)

    row = 5
    for i, ctrl in enumerate(result["controls"]):
        band = GREY if i % 2 else WHITE
        _cell(ws, row, 1, ctrl["framework"], fill=band)
        _cell(ws, row, 2, ctrl["control_id"], fill=band)
        _cell(ws, row, 3, ctrl["title"], fill=band, wrap=True)
        _cell(ws, row, 4, ctrl["state"], fill=band, bold=True)
        risk = ctrl.get("risk", "None")
        _cell(ws, row, 5, risk, fill=_risk_fill.get(risk, band),
              color=WHITE if risk in ("High", "Medium", "None") else "FF000000", bold=True)
        _cell(ws, row, 6, round(ctrl["best_similarity"], 3), fill=band)
        _cell(ws, row, 7, ctrl.get("narrative", ""), fill=band, wrap=True)
        row += 1

    widths = [14, 14, 34, 10, 10, 10, 60]
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:G{row - 1}"

    _cell(ws, row + 1, 1, "Â© 2024 Accenture | Confidential", color="FF505050", size=8)

    _write_crosswalk_sheet(wb, result)

    wb.save(out_path)
    return out_path


def _dash(s: str) -> str:
    """Normalize en/em dashes to a plain hyphen (deliverable convention)."""
    return (s or "").replace("â€”", "-").replace("â€“", "-")


def _write_crosswalk_sheet(wb, result):
    """Second sheet: for each Partial/Gap control, the mapped controls across frameworks."""
    ws = wb.create_sheet("Cross-Framework Gaps")

    ws.merge_cells("A1:I1")
    t = _cell(ws, 1, 1, "Missing / partial controls mapped across frameworks", bold=True, size=14)
    t.fill = PatternFill("solid", fgColor=WHITE)
    ws.cell(1, 1).border = Border(bottom=Side(style="medium", color=PURPLE))
    _cell(ws, 2, 1, "Each National Basic Cybersecurity Controls gap below implies the mapped NIST / ISO / NCA / CIS controls are also unmet.",
          color="FF505050")

    headers = ["National Basic Cybersecurity Controls", "Title", "State", "Risk",
               "NIST CSF 2.0", "ISO 27002:2022", "NCA ECC 2024", "NIST SP 800-53", "CIS v8.1"]
    for c, h in enumerate(headers, 1):
        _cell(ws, 4, c, h, bold=True, fill=PURPLE, color=WHITE, size=11)

    r = 5
    gaps = [c for c in result["controls"] if c["state"] in ("Partial", "Gap")]
    for i, ctrl in enumerate(gaps):
        band = GREY if i % 2 else WHITE
        risk = ctrl.get("risk", "None")
        _cell(ws, r, 1, ctrl["control_id"], fill=band, bold=True)
        _cell(ws, r, 2, ctrl["title"], fill=band, wrap=True)
        _cell(ws, r, 3, ctrl["state"], fill=band, bold=True)
        _cell(ws, r, 4, risk, fill=_risk_fill.get(risk, band),
              color=WHITE if risk in ("High", "Medium", "None") else "FF000000", bold=True)
        _cell(ws, r, 5, _dash(ctrl.get("nist_csf", "")), fill=band, wrap=True)
        _cell(ws, r, 6, _dash(ctrl.get("iso_27002", "")), fill=band, wrap=True)
        _cell(ws, r, 7, _dash(ctrl.get("nca_ecc", "")), fill=band, wrap=True)
        _cell(ws, r, 8, _dash(ctrl.get("nist_800_53", "")), fill=band, wrap=True)
        _cell(ws, r, 9, _dash(ctrl.get("cis", "")), fill=band, wrap=True)
        r += 1

    if not gaps:
        _cell(ws, r, 1, "No gaps or partial controls â€” full coverage.", color="FF1F9D55", bold=True)
        r += 1

    for c, w in enumerate([14, 30, 9, 9, 18, 20, 14, 18, 16], 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "A5"
    if gaps:
        ws.auto_filter.ref = f"A4:I{r - 1}"
    _cell(ws, r + 1, 1, "Â© 2024 Accenture | Confidential", color="FF505050", size=8)
