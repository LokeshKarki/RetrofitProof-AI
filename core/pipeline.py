"""One entry point used by the UI, the evaluation script and the tests."""
import time
from .schema import Circuit
from .graph import screen_safety, analyze_flags, is_safety
from .sizing import assess_cylinder
from .match import (enrich_from_legacy, build_like_for_like, build_instrumented,
                    build_electrification, group_bom, coverage, gaps)
from .io_migration import io_migration
from .tests_gen import generate_tests
from .roi import compute_roi


def analyze(circuit: Circuit, roi_assumptions: dict | None = None) -> dict:
    t0 = time.perf_counter()
    c = enrich_from_legacy(screen_safety(circuit))
    flags = analyze_flags(c)
    supply = c.requirements.supply_pressure_bar
    sizing = {x.id: assess_cylinder(x, supply) for x in c.components
              if x.type == "cylinder" and not is_safety(x)}
    for s in sizing.values():
        if s["existing_bore_ok"] is False:
            flags.append({"component_id": s["component_id"], "severity": "warning", "code": "UNDERSIZED",
                          "message": "Existing bore below theoretical requirement for stated load."})
    alts = [build_like_for_like(c, sizing), build_instrumented(c, sizing), build_electrification(c, sizing)]
    for a in alts:
        a["bom_grouped"] = group_bom(a["bom"])
        a["io"] = io_migration(c, a)
        a["tests"] = generate_tests(c, a, a["io"])
        a["roi"] = compute_roi(a["bom"], roi_assumptions)
    lfl = alts[0]["bom"]
    for l in lfl:
        x = l.get("xref")
        if x and x["conflict"] and l["part_number"] != "-":
            flags.append({"component_id": l["legacy_id"], "severity": "info", "code": "XREF_CONFLICT",
                          "message": f"Earlier requests gave different Festo answers for {l.get('legacy_part')}: "
                                     "ranked by evidence, confirm the choice."})
    flags.sort(key=lambda f: {"critical": 0, "warning": 1, "info": 2}[f["severity"]])
    return {"circuit": c, "flags": flags, "coverage": coverage(lfl), "gaps": gaps(lfl), "sizing": sizing, "alternatives": alts,
            "runtime_s": time.perf_counter() - t0}
