from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from core.binran_discrepancies import parse_candidates
from core.security import check_admin
from core.templates import templates

router = APIRouter()


@router.get("/admin/binran_discrepancies", response_class=HTMLResponse)
async def admin_binran_discrepancies(
    request: Request,
    faculty: str = "",
    q: str = "",
    _: str = Depends(check_admin),
):
    all_candidates = parse_candidates()
    faculties = list(dict.fromkeys(c["faculty"] for c in all_candidates if c["faculty"]))

    filtered = all_candidates
    if faculty:
        filtered = [c for c in filtered if c["faculty"] == faculty]
    if q:
        ql = q.lower()
        filtered = [c for c in filtered if ql in c["title"].lower() or ql in c["body"].lower()]

    return templates.TemplateResponse("admin/binran_discrepancies.html", {
        "request": request,
        "candidates": filtered,
        "faculties": faculties,
        "selected_faculty": faculty,
        "q": q,
        "total": len(all_candidates),
    })
