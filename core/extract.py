"""Document intelligence layer. The LLM only extracts; it never selects parts."""
import os, re, hashlib, io
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
from pydantic import ValidationError
from .schema import Circuit, Component, IOPoint

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")                      # .env lives in the project root, NOT inside .venv
CACHE = ROOT / "cache"

PROMPT = """You are reading a legacy pneumatic circuit diagram (ISO 1219) photographed or scanned.
Return JSON matching the schema.
Rules:
- Never guess. If a value is not visible, return null and LOWER the confidence for that item.
- Port naming: valves use ISO 5599 numbers ("1" supply, "2"/"4" working, "3"/"5" exhaust);
  cylinders use "A" (cap side / extend) and "B" (rod side / retract); service unit outlet is "OUT".
- connection_type "pneumatic" for air lines, "signal" for sensor-to-cylinder associations.
- List one entry per physical component with a short id (C1, V1, S1, SU1 ...). Copy visible labels.
- Do NOT decide whether something is safety-related with certainty: set safety_status to
  "potentially_safety_related" for dual-channel valves, E-stop/dump valves or anything that may be a
  safety function; otherwise "unknown".
- Put anything unclear in "unknowns"; put any assumption you had to make in "assumptions".
- confidence is 0.0-1.0 and must reflect legibility of the drawing, not your general certainty.
- Copy part numbers EXACTLY as printed (every dash, letter and digit). Never correct, complete or normalise a code.
  Put the competitor brand in "manufacturer" only if it is printed or the code format makes it certain.
- Component types: cylinder, valve, service_unit, sensor, fitting, pressure_switch, gauge, regulator, shutoff_valve,
  shuttle_valve, silencer, manifold, vacuum_ejector, vacuum_pad, accessory, other. Use "other" only as a last resort.
- If the drawing has numbered callouts and a parts list, set callout_no to the item number and make one component
  per item. Identical items stay separate components (the tool groups them later).
- For valves, state the position count and centre position in valve_function (for example
  "5/3 closed centre double solenoid"), read from the symbol, not from the part code."""



def _gemini(image_bytes: bytes, mime: str, prompt: str = PROMPT) -> str:
    import os
    import time

    from google import genai
    from google.genai import types
    from google.genai.errors import APIError

    client = genai.Client()
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    max_attempts = 3

    for attempt in range(max_attempts):
        try:
            print(
                f"[Gemini] Extraction attempt {attempt + 1}/{max_attempts} "
                f"using {model}"
            )

            resp = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type=mime
                    ),
                    prompt
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=Circuit
                )
            )

            if not resp.text:
                raise ValueError("Gemini returned an empty response")

            print("[Gemini] Extraction successful")
            return resp.text

        except APIError as exc:
            status = getattr(exc, "code", None)

            # Retry only transient server/rate-limit errors.
            if status not in (429, 500, 502, 503, 504):
                raise

            if attempt == max_attempts - 1:
                raise

            delay = 2 ** (attempt + 1)

            print(
                f"[Gemini] Temporary API error {status}. "
                f"Retrying in {delay} seconds..."
            )

            time.sleep(delay)

    raise RuntimeError("Gemini extraction failed after retries")



PROVIDERS = {"gemini": _gemini}                   # add "claude": ... later without touching callers


# ---------- PDF text layer ----------
_CALLOUT = re.compile(r"^\s*(\d{1,3})\s*:\s*(\S+)\s*$")


def pdf_text(data: bytes) -> str:
    """Text layer of a vector PDF. Empty for scans (then the vision model does everything)."""
    try:
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return ""


def parse_callouts(text: str) -> dict[int, str]:
    """'33: SY9320-5LOU-C10F-Q' lines -> {33: 'SY9320-5LOU-C10F-Q'}. One token per line, must contain a digit
    and a letter; free-text labels such as '10: DN 7,2 rychlospojka' are skipped on purpose."""
    out = {}
    for line in (text or "").splitlines():
        m = _CALLOUT.match(line)
        if m and re.search(r"\d", m.group(2)) and re.search(r"[A-Za-z]", m.group(2)) and len(m.group(2)) >= 4:
            out.setdefault(int(m.group(1)), m.group(2))
    return out


def reconcile_with_text_layer(c: Circuit, callouts: dict[int, str]) -> Circuit:
    """Deterministic backstop: the PDF text layer beats what the vision model typed.
    Fixes mis-copied codes, and adds a low-confidence component for any listed item the model skipped."""
    if not callouts:
        return c
    c = c.model_copy(deep=True)
    by_no = {x.callout_no: x for x in c.components if x.callout_no is not None}
    for n, code in callouts.items():
        comp = by_no.get(n)
        if comp is None:
            c.components.append(Component(id=f"P{n}", type="other", callout_no=n, part_number=code, confidence=0.5,
                                          label=f"Item {n} (in parts list, not found by the model)"))
            c.unknowns.append(f"Item {n} ({code}) is in the PDF parts list but the model did not return it. Check its type and connections.")
        elif (comp.part_number or "").strip().upper() != code.upper():
            c.assumptions.append(f"Item {n}: part number set from the PDF text layer ({code}); the model had {comp.part_number!r}.")
            comp.part_number = code
    return c


def extract_circuit(image_bytes: bytes, mime: str = "image/png", provider: str = "gemini",
                    use_cache: bool = True) -> Circuit:
    """Image or PDF in, Circuit out. For PDFs the text layer supplies exact part codes."""
    callouts = parse_callouts(pdf_text(image_bytes)) if mime == "application/pdf" else {}
    hint = ""
    if callouts:
        hint = ("\n\nThe PDF text layer lists these item numbers and part codes. They are exact. Use them for part_number "
                "and callout_no; use the drawing only for types, valve functions and connections:\n" +
                "\n".join(f"{n}: {code}" for n, code in sorted(callouts.items())))
    key = hashlib.md5(image_bytes + provider.encode() + hint.encode() + PROMPT.encode()).hexdigest()
    f = CACHE / f"{key}.json"
    if use_cache and f.exists():
        return Circuit.model_validate_json(f.read_text())
    prompt, last = PROMPT + hint, None
    for attempt in range(2):                       # one retry with the validation error fed back
        text = PROVIDERS[provider](image_bytes, mime, prompt)
        try:
            circuit = Circuit.model_validate_json(text)
            break
        except (ValidationError, ValueError) as e:
            last = e
            prompt = PROMPT + hint + f"\n\nYour previous answer was not valid for the schema ({str(e)[:300]}). Return valid JSON only."
    else:
        raise ValueError(f"Model returned invalid JSON twice: {last}")
    circuit = reconcile_with_text_layer(circuit, callouts)
    CACHE.mkdir(exist_ok=True)
    f.write_text(circuit.model_dump_json(indent=2))
    return circuit


def parse_io_list(file_or_bytes) -> list[IOPoint]:
    """CSV/XLSX with columns: tag, direction, linked_component, description, plc_address."""
    data = file_or_bytes.read() if hasattr(file_or_bytes, "read") else file_or_bytes
    try:
        df = pd.read_csv(io.BytesIO(data))
    except Exception:
        df = pd.read_excel(io.BytesIO(data))
    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.where(pd.notna(df), None)
    return [IOPoint(**{k: r.get(k) for k in ("tag", "direction", "linked_component", "description", "plc_address")})
            for r in df.to_dict("records")]


def load_case(case_dir: Path, source: str = "extracted") -> Circuit:
    c = Circuit.model_validate_json((case_dir / f"{source}.json").read_text())
    return c
