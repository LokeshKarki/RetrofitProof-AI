# RetrofitProof AI - extraction troubleshooting patch

## What this patch changes
- Runs the synchronous Gemini extraction call in a worker thread, so the ASGI event loop is not blocked.
- Rejects empty/unsupported files, diagrams larger than 25 MB, and PDFs with more than 20 pages.
- Returns clearer HTTP errors for invalid/encrypted PDFs and extraction failures.
- Logs the full extraction exception in the Uvicorn terminal.
- Adds a 3-minute browser-side timeout and clearer UI error messages.

## Apply
1. Stop the running server with Ctrl+C.
2. Back up your existing project folder.
3. Copy the contents of this project over your existing project, or replace only:
   - `server.py`
   - `web/app.js`
4. Keep your existing `.env` file in the project root. Do not put it in `.venv`.
5. Ensure `.env` contains a valid `GEMINI_API_KEY=...`.
6. Confirm `GEMINI_MODEL` is a model currently available to your API key in Google AI Studio. If unsure, remove the `GEMINI_MODEL` line and use a currently supported model name you have tested.
7. From the project root, activate the same virtual environment you used previously and run:
   `pip install -r requirements.txt`
8. Start:
   `uvicorn server:app --reload --port 8000`
9. Open `http://127.0.0.1:8000`, hard-refresh with Ctrl+Shift+R, and upload a small one-page PDF first.

## Diagnose one upload
- Open Chrome DevTools > Network, select `extract`, and inspect:
  - Request Headers: `Content-Type` should be `multipart/form-data; boundary=...`.
  - Payload: `image` should contain the PDF file; optional `io` should contain the CSV/XLSX.
  - Timing: if stalled under "Request sent", suspect upload/network/file size. If it stalls under "Waiting for server response", suspect Gemini/API processing.
  - Response: if the backend returns an error, read its JSON `error` field.
- Watch the Uvicorn terminal. With this patch, model/API failures print a traceback beginning with `Diagram extraction failed`.
- A successful extraction should be followed by `POST /api/extract ... 200`, then `POST /api/analyze ... 200`.

## Data behaviour
- Demo dropdown cases load precomputed `data/cases/*/extracted.json` files. Those are static/simulated.
- Uploading a PDF/image calls Gemini to extract a fresh circuit. The PDF text layer is also used to reconcile exact part numbers where possible.
- Analysis after extraction is deterministic Python: graph/risk checks, sizing, matching, alternatives, I/O migration, tests, ROI and export.
- Sizing uses `data/catalog.yaml`, which the README labels as DEMO catalogue data.
- Cross-reference uses `data/xref_evidence.csv` and rules in `core/xref.py`; it is historical evidence/rules, not live catalogue lookup.
- ROI defaults are placeholders until replaced with customer inputs.

## Important
The patch does not guarantee the selected Gemini model is available to your account, nor does it make an unsupported/oversized PDF valid. If the browser never sends `POST /api/extract` to Uvicorn, investigate the upload request in Network before debugging the Gemini code.
