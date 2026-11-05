"""API + static web UI.   Run:  uvicorn server:app --reload --port 8000   then open http://localhost:8000
Built on Starlette (the core of FastAPI) so no extra framework is needed."""
from pathlib import Path
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.responses import JSONResponse, FileResponse, Response
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
import logging

from core.schema import Circuit
from core.pipeline import analyze
from core.export import build_workbook, build_xref_workbook, BANNER
from core.xref import get_index, record_feedback
from core.xref_eval import evaluate_holdout
from core.extract import extract_circuit, parse_io_list
from core.roi import DEFAULTS
from eval import evaluate, CASES

logger = logging.getLogger("retrofitproof.extract")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB; raise only after testing larger drawings
MAX_PDF_PAGES = 20

ROOT = Path(__file__).parent
WEB = ROOT / "web"


def serialise(result: dict) -> dict:
    return {**result, "circuit": result["circuit"].model_dump()}


def bad(msg, code=422):
    return JSONResponse({"error": msg}, status_code=code)


async def meta(request):
    return JSONResponse({"banner": BANNER, "roi_defaults": DEFAULTS})


async def list_cases(request):
    names = sorted(d.name for d in CASES.iterdir() if (d / "extracted.json").exists())
    return JSONResponse(names)


async def get_case(request):
    d = CASES / request.path_params["name"]
    if not (d / "extracted.json").exists():
        return bad("unknown case", 404)
    img = next(iter(d.glob("diagram.*")), None)
    return JSONResponse({"circuit": Circuit.model_validate_json((d / "extracted.json").read_text()).model_dump(),
                         "image": f"/cases-img/{d.name}/{img.name}" if img else None})


async def do_analyze(request):
    body = await request.json()
    try:
        circuit = Circuit.model_validate(body["circuit"])
    except (ValidationError, KeyError) as e:
        return bad(f"Invalid circuit: {e}")
    return JSONResponse(serialise(analyze(circuit, body.get("roi_inputs"))))


async def do_extract(request):
    # This endpoint accepts one diagram plus an optional CSV/XLSX I/O list.
    # The request body is multipart/form-data; do not set Content-Type manually in browser JS.
    try:
        form = await request.form()
        img = form.get("image")
        if img is None or not hasattr(img, "read"):
            return bad("Diagram file is missing. Choose a PDF, PNG or JPG.", 400)

        filename = (getattr(img, "filename", "") or "diagram").lower()
        size = getattr(img, "size", None)
        if size is not None and size > MAX_UPLOAD_BYTES:
            return bad(f"Diagram is {size / (1024*1024):.1f} MB. Limit is {MAX_UPLOAD_BYTES // (1024*1024)} MB; compress or split the PDF.", 413)

        data = await img.read()
        if not data:
            return bad("The uploaded diagram is empty.", 400)
        if len(data) > MAX_UPLOAD_BYTES:
            return bad(f"Diagram exceeds the {MAX_UPLOAD_BYTES // (1024*1024)} MB upload limit.", 413)

        mime = "application/pdf" if filename.endswith(".pdf") else (img.content_type or "image/png")
        if mime not in ("application/pdf", "image/png", "image/jpeg", "image/webp"):
            return bad(f"Unsupported diagram type: {mime}. Upload PDF, PNG or JPG.", 415)

        # Reject unusually large PDFs before sending them to the vision model.
        if mime == "application/pdf":
            from pypdf import PdfReader
            import io
            try:
                page_count = len(PdfReader(io.BytesIO(data)).pages)
            except Exception as e:
                return bad(f"Could not read this PDF. It may be encrypted or damaged: {e}", 400)
            if page_count > MAX_PDF_PAGES:
                return bad(f"PDF has {page_count} pages. For this POC, upload a PDF with {MAX_PDF_PAGES} pages or fewer, or split it into smaller files.", 413)

        # Gemini SDK call is synchronous; run it in a worker thread so other API routes stay responsive.
        circuit = await run_in_threadpool(extract_circuit, data, mime)

        io_file = form.get("io")
        if io_file is not None and getattr(io_file, "filename", ""):
            io_data = await io_file.read()
            circuit.io = await run_in_threadpool(parse_io_list, io_data)

        return JSONResponse({"circuit": circuit.model_dump()})
    
    except Exception as e:
        logger.exception("Diagram extraction failed")

        from google.genai.errors import APIError

        if isinstance(e, APIError):
            status = getattr(e, "code", None)

            if status in (429, 503, 504):
                return JSONResponse(
                    {
                        "error": "Gemini is temporarily unavailable or overloaded.",
                        "details": str(e),
                        "retryable": True
                    },
                    status_code=503
                )

        return bad(
            f"Extraction failed: {type(e).__name__}: {e}",
            502
        )



async def do_export(request):
    body = await request.json()
    try:
        circuit = Circuit.model_validate(body["circuit"])
    except (ValidationError, KeyError) as e:
        return bad(f"Invalid circuit: {e}")
    result = analyze(circuit, body.get("roi_inputs"))
    xlsx = build_workbook(result, body.get("alt", "instrumented"))
    return Response(xlsx, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="retrofit_{body.get("alt","report")}.xlsx"'})


async def do_export_xref(request):
    body = await request.json()
    try:
        circuit = Circuit.model_validate(body["circuit"])
    except (ValidationError, KeyError) as e:
        return bad(f"Invalid circuit: {e}")
    xlsx = build_xref_workbook(analyze(circuit, body.get("roi_inputs")))
    return Response(xlsx, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="xref_request_format.xlsx"'})


async def xref_lookup(request):
    code = request.query_params.get("code", "")
    if not code.strip():
        return bad("code is required")
    return JSONResponse(get_index().lookup(code, request.query_params.get("competitor")))


async def xref_confirm(request):
    body = await request.json()
    try:
        entry = record_feedback(body.get("competitor"), body["code"], body["festo_type_code"], body["verdict"],
                                body.get("note", ""))
    except (KeyError, ValueError) as e:
        return bad(f"Invalid feedback: {e}")
    return JSONResponse({"saved": entry})


async def xref_stats(request):
    return JSONResponse(get_index().stats())


_HOLDOUT = {}


async def xref_eval(request):
    if "r" not in _HOLDOUT:                      # a few seconds on 14k rows, so compute once per server run
        _HOLDOUT["r"] = evaluate_holdout()
    return JSONResponse(_HOLDOUT["r"])


async def do_eval(request):
    rows = [evaluate(d) for d in sorted(CASES.iterdir()) if (d / "truth.json").exists()]
    return JSONResponse(rows)


async def index(request):
    return FileResponse(WEB / "index.html")


app = Starlette(routes=[
    Route("/", index),
    Route("/api/meta", meta),
    Route("/api/cases", list_cases),
    Route("/api/cases/{name}", get_case),
    Route("/api/analyze", do_analyze, methods=["POST"]),
    Route("/api/extract", do_extract, methods=["POST"]),
    Route("/api/export", do_export, methods=["POST"]),
    Route("/api/eval", do_eval),
    Route("/api/export_xref", do_export_xref, methods=["POST"]),
    Route("/api/xref/lookup", xref_lookup),
    Route("/api/xref/confirm", xref_confirm, methods=["POST"]),
    Route("/api/xref/stats", xref_stats),
    Route("/api/xref/eval", xref_eval),
    Mount("/cases-img", StaticFiles(directory=CASES), name="cases-img"),
    Mount("/web", StaticFiles(directory=WEB), name="web"),
])
