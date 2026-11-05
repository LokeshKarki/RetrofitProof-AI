# RetrofitProof AI (hackathon POC)

Decision support only. Part matches come from earlier cross-reference requests (with evidence counts); sizing uses a
DEMO catalog. Nothing here is a design approval.

## Run
- Create the virtual environment: `python -m venv .venv`
- Activate it: `source .venv/Scripts/activate` (Git Bash on Windows; PowerShell: `.venv\Scripts\Activate.ps1`; Linux/macOS: `source .venv/bin/activate`)
- Install dependencies: `python -m pip install -r requirements.txt`
- Add a `.env` in THIS folder (not `.venv`): `GEMINI_API_KEY=...` and optionally `GEMINI_MODEL=<model AI Studio lists for your key>`
- Regenerate demo cases: `python tools/make_cases.py` (3 cases; case 3 is the real SMC circuit)
- Start the server: `uvicorn server:app --reload --port 8000`
- Open http://localhost:8000

Other commands
- `python -m tests.test_core`      offline tests (series rules, lookup, every-part-gets-a-line, exports, feedback)
- `python -m core.xref_eval`       hold-out accuracy of the cross-reference on the real request data
- `python eval.py`                 pipeline smoke test on the demo cases (NOT accuracy evidence)

## What changed in this version
1. **Cross-reference knowledge base** (`core/xref.py`, `data/xref_evidence.csv`, `tools/build_xref.py`)
   - Exact lookup returns every Festo answer ever given for a competitor code, ranked, with "seen in N requests, M agree".
   - Series rules (SMC KQ2 fittings, AN silencers) answer codes never seen. Metric tube sizes only; they abstain otherwise.
   - Otherwise an explicit **No match: request datasheet**. Same-series codes are shown as hints, never as matches.
   - Four tiers from the wording of the Remarks: T1 direct, T2 equivalent but check differences, T3 suggested, T0 no match.
   - Your refined project sheet is loaded as *curated*: it outranks raw history, and its "no equivalent" decisions win.
2. **No part is dropped any more.** Every component produces a BOM row (fittings, silencers, pads, manifolds, switches...).
   Identical parts are grouped into one row with a quantity. A gaps panel lists everything with no answer.
3. **Real evidence beats the DEMO catalog.** If a competitor code has a direct/check/curated answer, that is the primary pick and the
   DEMO sizing result is kept as a check. Cylinders stay parametric because sizing can force an upsize.
4. **Schema**: new types (pressure_switch, gauge, regulator, shutoff_valve, shuttle_valve, silencer, manifold, vacuum_ejector,
   vacuum_pad, accessory), plus `callout_no` and `qty`.
5. **New risk flags**: `CLOSED_CENTRE_TRAPPED_PRESSURE` (5/3 closed-centre valves), `VACUUM_LOSS_LOAD_DROP` (vacuum pads) and
   `XREF_CONFLICT` when earlier requests disagreed. These are warnings, not safety escalation.
6. **Extraction**: accepts PDFs, reads the text layer for exact part codes, "copy part numbers exactly" prompt, one retry on invalid
   JSON, and a deterministic step that overrides mis-copied codes with the text-layer ones.
7. **Learning loop**: Confirm / Reject buttons write `data/feedback_log.jsonl`. Confirmed answers rank first next time, rejected ones sink.
8. **Export**: the Excel report has the like-for-like list in the same 18-column layout as the request sheets ("Xref (request format)"),
   plus a Gaps sheet. A standalone "Download cross-reference sheet" button gives only that sheet.
9. **Evaluation**: a real hold-out (split by request id) replaces the synthetic 1.00 as the headline. Caveats below.

## Honest numbers (hold-out, 1 in 5 requests held out)
- Only about 27% of matchable test rows carry a code seen in the other 80%: most codes are one-offs.
- On codes seen before: answer found 96% of the time, top-1 about 81%, top-3 about 83%.
- On codes never seen, only series rules can answer (about 1% of rows). That long tail is the next problem
  (datasheet parsing, embeddings, more series rules); this version does not solve it.
- Festo itself gave different answers to the same code in 203 of 960 repeated codes, so top-1 cannot reach 100%.
- Series rules were written after looking at this data, so their share is optimistic.

## Data care
- The request sheets contain customer-related fields. `.xlsx` files are git-ignored. The shipped `data/xref_evidence.csv` keeps only:
  competitor, code, description, Festo answer, tier, a cleaned technical remark, region, product type and a hashed request id.
  Dates, request/TRS ids, customer material numbers, countries and links are dropped.
  Check with the data owners that you may use it before sharing the repo or slides.
- `data/cases/case3_smc_vacuum_manifold/diagram.png` is the project 316 drawing with the title block (company, project, author) erased.
- Rebuild the evidence file: `python tools/build_xref.py --sep Sep_2026.xlsx --refined Project_316_parts_1.xlsx`

## Layout
- server.py            Starlette API + dashboard (`/api/xref/lookup`, `/api/xref/confirm`, `/api/xref/eval`, `/api/export_xref`, ...)
- web/                 dashboard (vanilla JS, no build step)
- core/xref.py         knowledge base, tiers, series rules, lookup, feedback log
- core/xref_eval.py    hold-out evaluation
- core/match.py        parametric matching + cross-reference lines, grouping, coverage, gaps
- core/graph.py        graph, safety screen, risk flags (incl. closed-centre and vacuum)
- core/extract.py      Gemini extraction (image or PDF), text-layer reconciliation
- core/sizing.py, io_migration.py, tests_gen.py, roi.py, export.py, pipeline.py
- data/catalog.yaml    DEMO records (DEMO-* part numbers), used only for sizing screens
- data/cases/          demo cases. Case 3 = real SMC circuit (44 parts). extracted.json files are SIMULATED extractions with deliberate errors.
- tests/test_core.py   offline tests

## Not done / roadmap
- Gemini extraction was not run here (no Google access from the build sandbox). PDF text-layer parsing and the reconcile step are tested offline.
- Default `GEMINI_MODEL` is now `gemini-3.8-flash` because Google's model page says 2.5 models are limited to existing users. Confirm in AI Studio.
- More series rules (SMC SY, AS, VHS, ZPT...) once verified against datasheets. Valve and vacuum parts are thin in the request data.
- Embedding search for fuzzy matches; use the confirm/reject log as training data.
- Case 3 topology was read by hand from the drawing, and its I/O list is an invented example.

