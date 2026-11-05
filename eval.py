"""python eval.py  -> measured results vs manually prepared reference models (3 judges-ready tables)."""
import json
from pathlib import Path
import pandas as pd
from core.schema import Circuit
from core.pipeline import analyze
from core.match import ESCALATED

CASES = Path(__file__).parent / "data" / "cases"


def prf(pred: set, truth: set):
    tp = len(pred & truth)
    return (tp / len(pred) if pred else 1.0), (tp / len(truth) if truth else 1.0)


def conn_key(k):
    return (k.from_component, k.to_component, k.connection_type)


def bom_map(res):
    lfl = next(a for a in res["alternatives"] if a["name"] == "like_for_like")
    out = {}
    for l in lfl["bom"]:
        out[l["legacy_id"]] = "ESCALATE" if l["status"] == ESCALATED else (
            None if l["part_number"] == "-" else l["part_number"])
    return out


def evaluate(case_dir: Path):
    truth = Circuit.model_validate_json((case_dir / "truth.json").read_text())
    ext = Circuit.model_validate_json((case_dir / "extracted.json").read_text())
    base = json.loads((case_dir / "expert_baseline.json").read_text())
    r = {"case": case_dir.name}
    tc = {(c.id, c.type) for c in truth.components}
    ec = {(c.id, c.type) for c in ext.components}
    r["component_precision"], r["component_recall"] = prf(ec, tc)
    r["connection_precision"], r["connection_recall"] = prf({conn_key(k) for k in ext.connections},
                                                            {conn_key(k) for k in truth.connections})
    for label, circ in (("raw_extraction", ext), ("after_correction", truth)):
        res = analyze(circ)
        got = bom_map(res)
        exp = base["like_for_like"]
        proposed = {k: v for k, v in got.items() if v not in (None,)}
        expected = {k: v for k, v in exp.items() if v is not None}
        hit = sum(1 for k, v in proposed.items() if expected.get(k) == v)
        r[f"{label}.match_precision"] = hit / len(proposed) if proposed else 1.0
        r[f"{label}.match_recall"] = hit / len(expected) if expected else 1.0
        escalated = {l["legacy_id"] for a in res["alternatives"] for l in a["bom"] if l["status"] == ESCALATED}
        r[f"{label}.safety_escalated"] = f"{len(escalated & set(base['truth_safety']))}/{len(base['truth_safety'])}"
        flags = {(f["code"], f["component_id"]) for f in res["flags"]}
        exp_flags = {tuple(x) for x in base["expected_flags"]}
        r[f"{label}.gap_detection_recall"] = len(flags & exp_flags) / len(exp_flags) if exp_flags else 1.0
        r[f"{label}.runtime_s"] = round(res["runtime_s"], 3)
    r["manual_baseline_min"] = base["manual_minutes"]
    return r


if __name__ == "__main__":
    rows = [evaluate(d) for d in sorted(CASES.iterdir()) if (d / "truth.json").exists()]
    df = pd.DataFrame(rows).set_index("case").T
    pd.set_option("display.float_format", lambda x: f"{x:.2f}")
    print(df.to_string())
    df.to_csv("eval_results.csv")
