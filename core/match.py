"""Deterministic, constraint-based matching with per-check evidence.
The LLM never selects parts; every proposal carries passed / failed / unknown checks."""
import math
import re
from functools import lru_cache
from pathlib import Path
import yaml

from .schema import Circuit
from .graph import is_safety, sensors_of, downstream_cylinders
from .sizing import G, SAFETY_FACTOR
from .xref import get_index

DATA = Path(__file__).resolve().parent.parent / "data"
FLOW_MARGIN = 1.5          # screening margin on average flow; change with engineer input
SPARE_IO = 0.2

PROVISIONAL = "Provisional candidate: not validated"
SCREEN_OK = "Passed screening checks: datasheet verification still required"
REJECTED = "Rejected by a failed check"
ESCALATED = "ESCALATED: not substituted, engineer review required"
UNRESOLVED = "UNRESOLVED: insufficient data"
# statuses for lines that come from the real cross-reference data (not from the DEMO catalog)
XREF_DIRECT = "Cross-reference: direct equivalent"
XREF_CHECK = "Cross-reference: equivalent, check differences"
XREF_SUGGEST = "Cross-reference: suggested, unverified"
NO_MATCH = "No match: request datasheet"
TIER_STATUS = {"T1": XREF_DIRECT, "T2": XREF_CHECK, "T3": XREF_SUGGEST}


@lru_cache(maxsize=1)
def catalog() -> list[dict]:
    return yaml.safe_load((DATA / "catalog.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def legacy_map() -> dict:
    rows = yaml.safe_load((DATA / "legacy_map.yaml").read_text(encoding="utf-8")) or []
    return {r["part_number"]: r for r in rows}


def by_cat(cat):
    return [x for x in catalog() if x["category"] == cat]


def chk(name, status, detail):
    return {"check": name, "status": status, "detail": detail}


def overall(checks):
    s = {c["status"] for c in checks}
    if "failed" in s:
        return REJECTED
    if "unknown" in s:
        return PROVISIONAL
    return SCREEN_OK


def line(alt, legacy_id, role, part, qty, rationale, checks, status=None):
    return {
        "legacy_part": None, "legacy_mfr": None, "legacy_label": None, "callout_no": None, "festo_article_no": None, "xref": None,
        "alt": alt, "legacy_id": legacy_id, "role": role,
        "part_number": part["part_number"] if part else "-",
        "manufacturer": part["manufacturer"] if part else "-",
        "qty": qty, "rationale": rationale,
        "status": status or (overall(checks) if part else UNRESOLVED),
        "verification_status": part["verification_status"] if part else "-",
        "unit_price_inr": part.get("price_inr") if part else None,
        "checks": checks,
    }


def enrich_from_legacy(c: Circuit) -> Circuit:
    """Fill missing valve fields from the legacy lookup when a legacy part number is known."""
    c = c.model_copy(deep=True)
    for comp in c.components:
        rec = legacy_map().get(comp.part_number or "")
        if rec:
            comp.valve_function = comp.valve_function or rec.get("valve_function")
            comp.voltage = comp.voltage or rec.get("voltage")
    return c


def _valve_sig(text):
    t = (text or "").lower().replace(" ", "")
    ways = re.search(r"\d/\d", t)
    act = "double" if "double" in t else "single" if "single" in t else None
    return (ways.group(0) if ways else None, act)


# ---------- cross-reference (real data) ----------
def xref_summary(res: dict) -> dict:
    """Compact, UI-ready view of a lookup result."""
    return {"query": res["query"], "method": res["method"], "n_requests": res["n_requests"],
            "n_no_match_requests": res["n_no_match_requests"], "conflict": res["conflict"], "note": res["note"],
            "candidates": res["candidates"][:3], "similar": res.get("similar", [])}


def _tag(ln, comp):
    ln["legacy_part"], ln["legacy_label"], ln["callout_no"] = comp.part_number, comp.label, comp.callout_no
    ln["legacy_mfr"] = comp.manufacturer
    if comp.qty > 1 and ln["qty"]:
        ln["qty"] *= comp.qty
    return ln


def _evidence_check(top):
    if top["method"] == "series_rule":
        return chk("Cross-reference evidence", "unknown",
                   "Series pattern only. This code was never seen in a request.")
    n, tot = top["n_requests"], top["n_total"]
    solid = top["curated"] or (n >= 2 and n / max(tot, 1) >= 0.8 and top["tier"] == "T1")
    return chk("Cross-reference evidence", "passed" if solid else "unknown",
               f"Seen in {n} request(s), {top['n_agree']} agree on this answer"
               + (f" (of {tot} with an answer)" if tot > n else "")
               + ("; confirmed by a technician or curated sheet" if top["curated"] else ""))


def xref_line(alt, comp, lead=""):
    """BOM line from the cross-reference data. Never drops a part: no hit becomes an explicit gap."""
    code = comp.part_number
    if not code:
        return _tag(line(alt, comp.id, comp.type, None, 1,
                         "No part number on the drawing, so it cannot be cross-referenced. Provide the code or datasheet.",
                         [], NO_MATCH), comp)
    res = get_index().lookup(code, comp.manufacturer)
    live = [c for c in res["candidates"] if not c["rejected"]]
    if not live:
        why = res["note"] or "No equivalent found."
        if res.get("similar") and "hints" not in why:
            why += " Same-series codes with answers are shown as hints only."
        ln = line(alt, comp.id, comp.type, None, 1, why, [], NO_MATCH)
        ln["xref"] = xref_summary(res)
        return _tag(ln, comp)
    top = live[0]
    part = {"part_number": top["festo_type_code"], "manufacturer": "Festo", "verification_status": "cross-reference"}
    checks = [_evidence_check(top)]
    if top["remark"]:
        checks.append(chk("Compatibility differences", "unknown", top["remark"]))
    if len(live) > 1:
        checks.append(chk("Other answers seen", "unknown", "; ".join(
            f"{c['festo_type_code']} ({c['n_requests']} req)" for c in live[1:3])))
    checks.append(chk("Datasheet verification", "unknown", "Cross-reference, not a datasheet-verified match."))
    why = (lead + " " if lead else "") + (
        f"{top['tier_label']}: {top['festo_type_code']}"
        + (" (series pattern)" if top["method"] == "series_rule" else f", seen in {top['n_requests']} request(s)"))
    ln = line(alt, comp.id, comp.type, part, 1, why, checks, TIER_STATUS[top["tier"]])
    ln["festo_article_no"] = top["festo_article_no"] or None
    ln["xref"] = xref_summary(res)
    return _tag(ln, comp)


def attach_xref(ln, comp):
    """Second, independent method for parametric lines: show what the real cross-reference data says."""
    if not comp.part_number:
        return ln
    res = get_index().lookup(comp.part_number, comp.manufacturer, use_rules=False)
    live = [c for c in res["candidates"] if not c["rejected"]]
    if live:
        top = live[0]
        ln["xref"] = xref_summary(res)
        ln["checks"] = ln["checks"] + [chk(
            "Cross-reference (real data)", "unknown",
            f"Earlier requests answer {comp.part_number} with {top['festo_type_code']} ({top['tier_label']}, "
            f"{top['n_requests']} request(s)). The parametric pick above is a DEMO catalog part: compare the two.")]
    return ln


# ---------- component matchers ----------
def match_cylinder(alt, comp, sz):
    req = sz["required_bore_mm"]
    existing = comp.bore_mm
    target = max(existing or 0, req or 0)
    if not target:
        return line(alt, comp.id, "cylinder", None, 1, "Bore and load unknown.", [],
                    UNRESOLVED), None
    cands = sorted([x for x in by_cat("cylinder") if x["bore_mm"] >= target], key=lambda x: x["bore_mm"])
    if not cands:
        return line(alt, comp.id, "cylinder", None, 1, f"No catalog bore >= {target:.0f} mm.", [],
                    UNRESOLVED), None
    p = cands[0]
    p_bar = sz["inputs"]["pressure_bar"]
    checks = []
    if req is None:
        checks.append(chk("Bore vs load", "unknown", "No load stated."))
    elif p["bore_mm"] >= req:
        f_ext, f_ret = (None, None)
        if p_bar:
            from .sizing import cylinder_force_n
            f_ext, f_ret = cylinder_force_n(p["bore_mm"], p["rod_mm"], p_bar)
        checks.append(chk("Bore vs load", "passed",
                          f"Bore {p['bore_mm']:.0f} mm >= theoretical {req} mm"
                          + (f"; est. force ext {f_ext:.0f} N / ret {f_ret:.0f} N" if f_ext else "")))
    else:
        checks.append(chk("Bore vs load", "failed", f"Bore {p['bore_mm']} < theoretical {req} mm"))
    if comp.stroke_mm is None:
        checks.append(chk("Stroke available", "unknown", "Existing stroke unknown."))
    else:
        ok = p["stroke_min_mm"] <= comp.stroke_mm <= p["stroke_max_mm"]
        checks.append(chk("Stroke available", "passed" if ok else "failed",
                          f"{comp.stroke_mm:.0f} mm within {p['stroke_min_mm']}-{p['stroke_max_mm']} mm"
                          if ok else "Stroke outside catalog range"))
    if p_bar:
        ok = p["max_pressure_bar"] >= p_bar
        checks.append(chk("Pressure rating", "passed" if ok else "failed",
                          f"{p_bar} bar vs rated {p['max_pressure_bar']} bar"))
    else:
        checks.append(chk("Pressure rating", "unknown", "Operating pressure unknown."))
    checks.append(chk("Mounting compatibility", "unknown", "Not assessed: verify footprint/rod end."))
    checks.append(chk("Buckling / speed / cushioning", "unknown", "Needs engineering verification."))
    why = f"Smallest catalog bore >= {target:.0f} mm"
    if existing and req and existing < req:
        why += " (existing bore is below the theoretical requirement, so upsized)"
    elif existing:
        why += f" (like-for-like on existing {existing:.0f} mm bore)"
    return line(alt, comp.id, "cylinder", p, 1, why, checks), p


def match_valve(alt, c: Circuit, comp, sizing_by_id, cyl_part_by_id):
    sig = _valve_sig(comp.valve_function)
    if sig[0] is None or sig[1] is None and sig[0] == "5/2":
        return line(alt, comp.id, "valve", None, 1, "Valve function unspecified or ambiguous.", [], UNRESOLVED)
    cyls = downstream_cylinders(c, comp.id)
    flows = [sizing_by_id[x]["required_flow_lpm"] for x in cyls if x in sizing_by_id]
    flow_req = None
    if cyls and all(f is not None for f in flows):
        flow_req = sum(flows) * FLOW_MARGIN
    cands = [v for v in by_cat("valve") if _valve_sig(v["function"]) == sig or
             (sig[1] is None and _valve_sig(v["function"])[0] == sig[0])]
    if flow_req:
        cands = [v for v in cands if v["flow_lpm"] >= flow_req]
    if not cands:
        return line(alt, comp.id, "valve", None, 1,
                    f"No catalog valve with function {comp.valve_function} and flow >= {flow_req}.", [], UNRESOLVED)
    want_port = next((cyl_part_by_id[x]["port"] for x in cyls if x in cyl_part_by_id), None)
    rank = {"G1/8": 1, "G1/4": 2, "G3/8": 3, "G1/2": 4}
    cands.sort(key=lambda v: (abs(rank.get(v["port"], 9) - rank.get(want_port, rank.get(v["port"], 9))),
                              v["flow_lpm"]))
    v = cands[0]
    checks = [chk("Valve function", "passed", f"{comp.valve_function} -> {v['function']}")]
    if not comp.voltage:
        checks.append(chk("Coil voltage", "unknown", "Existing coil voltage unknown (catalog is 24VDC)."))
    else:
        ok = "24" in comp.voltage and "dc" in comp.voltage.lower()
        checks.append(chk("Coil voltage", "passed" if ok else "failed", f"{comp.voltage} vs {v['voltage']}"))
    if flow_req:
        checks.append(chk("Flow capacity", "passed",
                          f"Rated {v['flow_lpm']} l/min >= {flow_req:.0f} (avg {flow_req/FLOW_MARGIN:.0f} x margin {FLOW_MARGIN})"))
    else:
        checks.append(chk("Flow capacity", "unknown", "Stroke time/stroke/bore missing or no downstream cylinder."))
    if want_port:
        checks.append(chk("Port size", "passed" if v["port"] == want_port else "unknown",
                          f"Valve {v['port']} vs cylinder {want_port}" +
                          ("" if v["port"] == want_port else " (adapter fitting needed)")))
    checks.append(chk("Pressure rating", "passed" if (c.requirements.supply_pressure_bar or 0) <= v["max_pressure_bar"]
                      and c.requirements.supply_pressure_bar else "unknown",
                      f"Rated {v['max_pressure_bar']} bar"))
    checks.append(chk("Mounting / manifold compatibility", "unknown", "Not assessed."))
    return line(alt, comp.id, "valve", v, 1, "Same function, smallest valve meeting flow and port.", checks)


def match_service_unit(alt, c: Circuit, comp, sizing_by_id):
    flows = [s["required_flow_lpm"] for s in sizing_by_id.values()]
    known = [f for f in flows if f]
    need = sum(known) * FLOW_MARGIN if known else None
    cands = sorted(by_cat("service_unit"), key=lambda x: x["flow_lpm"])
    if need:
        cands = [x for x in cands if x["flow_lpm"] >= need]
    if not cands:
        return line(alt, comp.id, "service_unit", None, 1, "No catalog service unit large enough.", [], UNRESOLVED)
    p = cands[0]
    checks = [chk("Flow capacity", "passed" if need else "unknown",
                  (f"Rated {p['flow_lpm']} >= {need:.0f} l/min (all cylinders assumed simultaneous)" if need
                   else "No cylinder flow known."))]
    if len(known) != len(flows):
        checks.append(chk("Completeness", "unknown", "Some cylinders have no flow estimate."))
    checks.append(chk("Pressure range / filtration", "unknown", "Verify regulator range and filter grade."))
    return line(alt, comp.id, "service_unit", p, 1, "Smallest unit covering summed cylinder flow.", checks)


def match_sensor(alt, comp):
    p = by_cat("sensor")[0]
    return line(alt, comp.id, "sensor", p, 1, "Slot proximity sensor, PNP 24VDC.",
                [chk("Output type", "unknown", "Existing sensor output (PNP/NPN) unknown."),
                 chk("Mounting", "unknown", "Cylinder slot/profile not verified.")])


# ---------- alternatives ----------
def _escalated(alt, comp):
    return line(alt, comp.id, comp.type, None, 0,
                "Retain existing item until a safety engineer provides approved requirements.",
                [], ESCALATED)


def _prefer_xref(ln, comp, alt):
    """Real cross-reference evidence beats the DEMO catalog. The parametric result is kept as a check."""
    if not comp.part_number:
        return _tag(ln, comp)
    xl = xref_line(alt, comp)
    if xl["part_number"] != "-" and (xl["status"] in (XREF_DIRECT, XREF_CHECK) or xl["xref"]["candidates"][0]["curated"]):
        xl["checks"].append(chk("Parametric screening", "unknown",
                                f"DEMO-catalog screening says: {ln['status']}"
                                + (f" ({ln['part_number']})" if ln["part_number"] != "-" else "")
                                + ". Not used because real cross-reference data exists for this code."))
        return xl
    return attach_xref(_tag(ln, comp), comp)


def _like_for_like(c, sizing, alt="like_for_like"):
    bom, cyl_parts = [], {}
    for comp in c.components:
        if comp.type == "cylinder" and not is_safety(comp):
            ln, p = match_cylinder(alt, comp, sizing[comp.id])
            bom.append(attach_xref(_tag(ln, comp), comp))
            if p:
                cyl_parts[comp.id] = p
    for comp in c.components:
        if comp.type == "cylinder" and not is_safety(comp):
            continue
        if is_safety(comp):
            bom.append(_tag(_escalated(alt, comp), comp))
        elif comp.type == "valve":
            bom.append(_prefer_xref(match_valve(alt, c, comp, sizing, cyl_parts), comp, alt))
        elif comp.type == "service_unit":
            bom.append(_prefer_xref(match_service_unit(alt, c, comp, sizing), comp, alt))
        elif comp.type == "sensor":
            bom.append(_prefer_xref(match_sensor(alt, comp), comp, alt))
        else:
            bom.append(xref_line(alt, comp))       # fittings, silencers, pads, switches...: never dropped
    return bom


def group_bom(bom):
    """Collapse identical parts into one row with a quantity and the list of drawing items."""
    out, index = [], {}
    for l in bom:
        key = (l["role"], l.get("legacy_part"), l["part_number"], l["status"], l["rationale"])
        if key in index:
            g = out[index[key]]
            g["qty"] += l["qty"]
            g["legacy_ids"].append(l["legacy_id"])
        else:
            index[key] = len(out)
            out.append({**l, "legacy_ids": [l["legacy_id"]]})
    for g in out:
        g["legacy_id"] = ", ".join(g["legacy_ids"])
    return out


def coverage(bom):
    """Share of parts that got a Festo answer, split by how strong the evidence is."""
    lines = [l for l in bom if l["role"] != "sensor (new)"]
    kinds = {"direct": 0, "check": 0, "suggested": 0, "parametric": 0, "no_match": 0, "escalated": 0}
    for l in lines:
        st = l["status"]
        if st == ESCALATED:
            kinds["escalated"] += 1
        elif st == XREF_DIRECT:
            kinds["direct"] += 1
        elif st == XREF_CHECK:
            kinds["check"] += 1
        elif st == XREF_SUGGEST:
            kinds["suggested"] += 1
        elif l["part_number"] == "-":
            kinds["no_match"] += 1
        else:
            kinds["parametric"] += 1
    scope = len(lines) - kinds["escalated"]
    matched = scope - kinds["no_match"]
    return {"lines": len(lines), "in_scope": scope, "matched": matched, "pct": (matched / scope) if scope else None, **kinds}


def gaps(bom):
    """Parts with no Festo answer, grouped by competitor code, for the gaps panel."""
    out, idx = [], {}
    for l in bom:
        if l["part_number"] != "-" or l["status"] == ESCALATED or l["role"] == "sensor (new)":
            continue
        key = (l.get("legacy_part") or l["legacy_id"], l["role"])
        if key not in idx:
            idx[key] = len(out)
            out.append({"legacy_ids": [], "legacy_part": l.get("legacy_part"), "role": l["role"],
                        "label": l.get("legacy_label"), "reason": l["rationale"],
                        "similar": (l.get("xref") or {}).get("similar", [])})
        out[idx[key]]["legacy_ids"].append(l["legacy_id"])
    return out


def build_like_for_like(c, sizing):
    return {"name": "like_for_like", "title": "Like-for-like replacement",
            "bom": _like_for_like(c, sizing),
            "notes": ["Replaces components one-to-one with the closest functional equivalent."]}


def build_instrumented(c, sizing):
    alt = "instrumented"
    bom = [dict(x, alt=alt) for x in _like_for_like(c, sizing, alt)]
    sensor_p = by_cat("sensor")[0]
    new_di = 0
    for comp in c.components:
        if comp.type == "cylinder" and not is_safety(comp):
            missing = max(0, 2 - len(sensors_of(c, comp.id)))
            if missing:
                bom.append(line(alt, comp.id, "sensor (new)", sensor_p, missing,
                                f"Adds {missing} end-position sensor(s) for {comp.id}.",
                                [chk("Mounting", "unknown", "Cylinder slot/profile not verified.")]))
                new_di += missing
    di = sum(1 for p in c.io if p.direction == "DI") + new_di
    do = sum(1 for p in c.io if p.direction == "DO")
    notes = ["Adds missing end-position sensors and remote I/O so signals reach an Ethernet-based controller."]
    for direction, count, cat_key in (("DI", di, "di_channels"), ("DO", do, "do_channels")):
        if count:
            mod = next(m for m in by_cat("remote_io") if m[cat_key] > 0)
            n = math.ceil(count * (1 + SPARE_IO) / mod[cat_key])
            bom.append(line(alt, "-", f"remote I/O ({direction})", mod, n,
                            f"{count} {direction} points + {int(SPARE_IO*100)}% spare over {mod[cat_key]} ch/module.",
                            [chk("Fieldbus match", "unknown", f"Catalog bus: {mod['bus']}; confirm target controller: "
                                 f"{c.requirements.target_controller or 'not stated'}"),
                             chk("Channel capacity", "passed", f"{n} x {mod[cat_key]} >= {count}")]))
    return {"name": "instrumented", "title": "Instrumented upgrade", "bom": bom, "notes": notes}


def build_electrification(c, sizing):
    alt = "electrification"
    bom = []
    for comp in c.components:
        if comp.type != "cylinder":
            continue
        if is_safety(comp):
            bom.append(_escalated(alt, comp))
            continue
        if not (comp.load_kg and comp.stroke_mm):
            bom.append(line(alt, comp.id, "electric axis", None, 1, "Load or stroke unknown.", [], UNRESOLVED))
            continue
        force = comp.load_kg * G * SAFETY_FACTOR
        cands = sorted([a for a in by_cat("electric_axis")
                        if a["force_n"] >= force and a["stroke_max_mm"] >= comp.stroke_mm],
                       key=lambda a: a["force_n"])
        if not cands:
            bom.append(line(alt, comp.id, "electric axis", None, 1,
                            "No catalog axis meets force/stroke (screening).", [], UNRESOLVED))
            continue
        a = cands[0]
        bom.append(line(alt, comp.id, "electric axis", a, 1,
                        f"SCREENING ONLY: force >= {force:.0f} N, stroke >= {comp.stroke_mm:.0f} mm.",
                        [chk("Force / stroke", "passed", f"{a['force_n']} N, {a['stroke_max_mm']} mm"),
                         chk("Speed / duty cycle / mass moment", "unknown", "Needs engineering study."),
                         chk("Motor controller, cabling, mounting", "unknown", "Not included in this BOM.")],
                        status="SCREENING ONLY: needs engineering study"))
    return {"name": "electrification", "title": "Electrification (screening only)", "bom": bom,
            "notes": ["Indicative only. Not a design. Motor controller, cabling and mechanical interfaces excluded."]}
