"""PDF renderer for monthly/annual reports — reportlab, single A4 output.

Takes the JSON report dict from services.reporting and renders a clean,
professional PDF: header, period/scope, provenance, summary, breakdown
tables, space weather, decisions, and the honesty notes as a footer block.
"""
from io import BytesIO
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

NAVY = colors.HexColor("#0A2540")
BLUE = colors.HexColor("#1B6CA8")
CYAN = colors.HexColor("#15A0C8")
SUB = colors.HexColor("#5A6B7B")
RULE = colors.HexColor("#D6E0EA")
AMBER_BG = colors.HexColor("#FFF7E8")
AMBER_BD = colors.HexColor("#E0A030")

S_TITLE = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=17, textColor=NAVY, leading=21)
S_SUB = ParagraphStyle("s", fontName="Helvetica", fontSize=9, textColor=SUB, leading=12)
S_H = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=11, textColor=BLUE,
                     leading=14, spaceBefore=10, spaceAfter=4)
S_BODY = ParagraphStyle("b", fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#1A2733"), leading=12)
S_NOTE = ParagraphStyle("n", fontName="Helvetica-Oblique", fontSize=7.5, textColor=SUB, leading=10)
S_CELL = ParagraphStyle("c", fontName="Helvetica", fontSize=8, leading=10)


def _fmt_pc(v: Optional[float]) -> str:
    return f"{v:.2e}" if v is not None else "—"


def _fmt(v, suffix="") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.1f}{suffix}"
    return f"{v:,}{suffix}"


def _table(data: List[List[str]], widths: List[float], header: bool = True) -> Table:
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#1A2733")),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    t.setStyle(TableStyle(style))
    return t


def render_report_pdf(report: Dict[str, Any]) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"CAS {report['report_type'].title()} Report {report['period']['label']}",
        author="CAS Platform",
    )
    story: list = []

    rtype = report["report_type"].title()
    period = report["period"]["label"]
    scope = report["scope"]["mode"]

    story.append(Paragraph("CAS PLATFORM", ParagraphStyle(
        "brand", fontName="Helvetica-Bold", fontSize=10, textColor=CYAN, leading=12)))
    story.append(Paragraph(f"{rtype} Operational Report — {period}", S_TITLE))
    prov = report.get("provenance", {})
    ow = prov.get("observation_window", {}) or {}
    story.append(Paragraph(
        f"Scope: {scope} ({report['scope'].get('satellites', 0)} satellites)  ·  "
        f"Generated: {str(prov.get('generated_at', ''))[:16]}Z  ·  "
        f"Observation window: {str(ow.get('first_observation', ''))[:10]} → "
        f"{str(ow.get('last_observation', ''))[:10]} ({ow.get('days_observing', '—')} days)",
        S_SUB))
    story.append(HRFlowable(width="100%", thickness=1.2, color=CYAN, spaceBefore=4, spaceAfter=6))

    # Summary
    s = report.get("summary", {})
    story.append(Paragraph("1 · Conjunction Activity (tracked high-risk)", S_H))
    story.append(_table(
        [["Unique conjunctions", "CDM updates", "Max Pc", "Min miss (m)", "Median miss (m)"],
         [_fmt(s.get("unique_conjunctions")), _fmt(s.get("cdm_updates")),
          _fmt_pc(s.get("max_pc")), _fmt(s.get("min_miss_m")), _fmt(s.get("median_miss_m"))]],
        [34 * mm, 30 * mm, 30 * mm, 30 * mm, 34 * mm]))

    # Satellite breakdown (operator) or top objects (admin)
    sats = report.get("satellites")
    if sats is not None:
        story.append(Paragraph("2 · Your Satellites — Activity Breakdown", S_H))
        if sats:
            rows = [["Satellite", "NORAD", "Conjunctions", "Max Pc", "Min miss (m)"]]
            for x in sats[:15]:
                rows.append([x["sat_name"][:34], x["norad_id"],
                             _fmt(x["unique_conjunctions"]), _fmt_pc(x["max_pc"]),
                             _fmt(x["min_miss_m"])])
            story.append(_table(rows, [62 * mm, 20 * mm, 28 * mm, 26 * mm, 26 * mm]))
        else:
            story.append(Paragraph("No conjunction activity recorded for your watchlist this period.", S_BODY))

        tc = report.get("top_counterparties") or []
        story.append(Paragraph("3 · Top Counterparty Objects", S_H))
        if tc:
            rows = [["Object", "NORAD", "Conjunctions", "Max Pc"]]
            for x in tc[:10]:
                rows.append([x["name"][:40] or "—", x["norad_id"],
                             _fmt(x["unique_conjunctions"]), _fmt_pc(x["max_pc"])])
            story.append(_table(rows, [78 * mm, 22 * mm, 30 * mm, 28 * mm]))
        else:
            story.append(Paragraph("—", S_BODY))
    else:
        to = report.get("top_objects") or []
        story.append(Paragraph("2 · Most Conjunction-Active Objects (global)", S_H))
        if to:
            rows = [["Object", "NORAD", "Conjunctions", "Max Pc"]]
            for x in to[:10]:
                rows.append([x["name"][:40] or "—", x["norad_id"],
                             _fmt(x["unique_conjunctions"]), _fmt_pc(x["max_pc"])])
            story.append(_table(rows, [78 * mm, 22 * mm, 30 * mm, 28 * mm]))

    # Decisions
    d = report.get("decisions", {})
    idx = "4" if sats is not None else "3"
    story.append(Paragraph(f"{idx} · Decision Activity", S_H))
    by_action = d.get("by_action") or {}
    if by_action:
        rows = [["Recorded action", "Count"]]
        rows += [[k, _fmt(v)] for k, v in by_action.items()]
        story.append(_table(rows, [100 * mm, 30 * mm]))
        mrh = d.get("median_response_hours")
        story.append(Paragraph(
            f"Median response time: {(_fmt(mrh, ' h') if mrh is not None else '— (insufficient sample)')} "
            f"· sample n={d.get('response_sample_n', 0)}", S_BODY))
    else:
        story.append(Paragraph("No operator actions recorded in this period.", S_BODY))
    story.append(Paragraph(d.get("note", ""), S_NOTE))

    # Space weather
    w = report.get("space_weather", {})
    idx2 = "5" if sats is not None else "4"
    story.append(Paragraph(f"{idx2} · Space Weather Context", S_H))
    story.append(_table(
        [["Snapshots", "Kp avg", "Kp max", "F10.7 max", "Days Kp ≥ 5"],
         [_fmt(w.get("snapshots")), _fmt(w.get("kp_avg")), _fmt(w.get("kp_max")),
          _fmt(w.get("f107_max")), _fmt(w.get("elevated_days_kp_ge5"))]],
        [31 * mm, 31 * mm, 31 * mm, 33 * mm, 32 * mm]))

    # Annual trend
    trend = report.get("monthly_trend")
    if trend:
        idx3 = "6" if sats is not None else "5"
        story.append(Paragraph(f"{idx3} · Month-by-Month Trend", S_H))
        rows = [["Month", "Unique conjunctions", "Max Pc", "Min miss (m)", "Actions"]]
        for t in trend:
            rows.append([f"{t['month']:02d}", _fmt(t["unique_conjunctions"]),
                         _fmt_pc(t["max_pc"]), _fmt(t["min_miss_m"]), _fmt(t["actions"])])
        story.append(_table(rows, [20 * mm, 42 * mm, 34 * mm, 34 * mm, 28 * mm]))

    # Honesty notes footer
    story.append(Spacer(1, 6))
    notes = report.get("notes") or []
    note_cell = [[Paragraph("Scope & honesty notes", ParagraphStyle(
        "nh", fontName="Helvetica-Bold", fontSize=8.5, textColor=colors.HexColor("#7A5500")))]]
    for n in notes:
        note_cell.append([Paragraph(f"– {n}", ParagraphStyle(
            "nn", fontName="Helvetica", fontSize=7.5, textColor=colors.HexColor("#5A4B6B"), leading=10))])
    nt = Table(note_cell, colWidths=[158 * mm])
    nt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AMBER_BG),
        ("BOX", (0, 0), (-1, -1), 0.8, AMBER_BD),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(nt)
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "CAS Platform · casplatform.com · conjunction decision support — human-in-the-loop. "
        "Report generated from tracked public-catalogue data.", S_NOTE))

    doc.build(story)
    return buf.getvalue()
