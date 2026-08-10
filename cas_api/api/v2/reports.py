"""Operator reporting endpoints — /api/v2/reports/*

TÜBİTAK UZAY feedback #2: monthly/annual reporting outputs for process
management and retrospective analysis.

- GET /api/v2/reports/monthly?year&month   (auth; operator=watchlist-scoped, admin=global)
- GET /api/v2/reports/annual?year          (auth; same scoping + monthly trend)
- GET /api/v2/reports/info                 (capability description, authenticated)

Endpoints are sync (def) so blocking psycopg2 runs in FastAPI's threadpool.
"""
from typing import Any, Dict, Union

from fastapi import APIRouter, Depends, Query, Response

from core.auth import CurrentUser, get_current_user, require_reporting
from services.reporting import annual_report, monthly_report
from services.report_pdf import render_report_pdf
from services.report_xlsx import render_report_xlsx


def _pdf_response(report: Dict[str, Any]) -> Response:
    fname = f"CAS_{report['report_type']}_report_{report['period']['label']}.pdf"
    return Response(
        content=render_report_pdf(report),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _xlsx_response(report: Dict[str, Any]) -> Response:
    fname = f"CAS_{report['report_type']}_report_{report['period']['label']}.xlsx"
    return Response(
        content=render_report_xlsx(report),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _formatted(report: Dict[str, Any], fmt: str):
    if fmt == "pdf":
        return _pdf_response(report)
    if fmt == "xlsx":
        return _xlsx_response(report)
    return report

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/monthly", response_model=None)
def reports_monthly(
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    format: str = Query("json", pattern="^(json|pdf|xlsx)$"),
    user: CurrentUser = Depends(require_reporting("monthly")),
) -> Union[Dict[str, Any], Response]:
    report = monthly_report(year, month, user_id=user.id, is_admin=(user.role == "admin"))
    return _formatted(report, format)


@router.get("/annual", response_model=None)
def reports_annual(
    year: int = Query(..., ge=2020, le=2100),
    format: str = Query("json", pattern="^(json|pdf|xlsx)$"),
    user: CurrentUser = Depends(require_reporting("full")),
) -> Union[Dict[str, Any], Response]:
    report = annual_report(year, user_id=user.id, is_admin=(user.role == "admin"))
    return _formatted(report, format)


@router.get("/info")
def reports_info(user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    return {
        "module": "reports",
        "endpoints": {
            "monthly": "/api/v2/reports/monthly?year=YYYY&month=M (auth required)",
            "annual": "/api/v2/reports/annual?year=YYYY (auth required)",
        },
        "scope": "operator: watchlist-scoped; admin: global; empty watchlist: empty report (fail-closed)",
        "is": [
            "Periodic aggregation over tracked high-risk (RED) conjunction activity",
            "Decision activity summary from recorded operator actions",
            "Space-weather context for the period",
        ],
        "is_not": [
            "NOT a forecast — retrospective reporting only",
            "NOT all catalogued conjunctions — pipeline ingests Pc >= 1e-4",
            "NOT an autonomous recommendation — decision-support documentation",
        ],
    }
