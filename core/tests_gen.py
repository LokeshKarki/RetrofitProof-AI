"""Traceable FAT/SAT proposals. Every row names the requirement or assumption it verifies.
All rows are PROPOSED; none is an approved acceptance criterion until an engineer signs off."""
from .schema import Circuit
from .graph import sensors_of, is_safety
from .match import ESCALATED

PROPOSED = "Proposed: not an approved acceptance criterion"


def _row(n, alt, comp, req, test, steps, criterion, assumption):
    return {"test_id": f"T{n:03d}", "alt": alt, "component": comp, "requirement": req, "test": test,
            "procedure": steps, "pass_criterion": criterion, "validates_assumption": assumption,
            "status": PROPOSED, "result": "", "signed_off_by": ""}


def generate_tests(c: Circuit, alt: dict, io_rows: list[dict]) -> list[dict]:
    rows, n = [], 0
    comps = {x.id: x for x in c.components}
    for ln in alt["bom"]:
        cid = ln["legacy_id"]
        comp = comps.get(cid)
        if ln["status"] == ESCALATED:
            n += 1
            rows.append(_row(n, alt["name"], cid, "Approved safety requirements (not yet provided)",
                             "NO TEST GENERATED",
                             "Engineer to supply approved safety requirements and risk assessment.",
                             "n/a", "Safety function must not be validated from an AI-extracted drawing."))
            continue
        if comp is None or ln["part_number"] == "-":
            continue
        if ln["role"] == "cylinder":
            ts = f"<= {comp.stroke_time_s} s" if comp.stroke_time_s else "TBD: stroke time not stated by customer"
            tests = [
                (f"R-{cid}-STROKE", "Stroke time check",
                 "Cycle cylinder 10x at working pressure; time each stroke.", f"Stroke time {ts} each direction",
                 "Required stroke time and valve flow estimate"),
                (f"R-{cid}-ENDPOS", "End-position confirmation",
                 "Drive to each end; confirm sensor state at PLC.",
                 "Both end states reported correctly" if len(sensors_of(c, cid)) >= 2 or alt["name"] == "instrumented"
                 else "Available end(s) reported; unmonitored end documented",
                 "Sensor wiring and I/O migration"),
                (f"R-{cid}-LEAK", "Leak-down test",
                 "Pressurise, isolate supply, record decay over site-defined time.",
                 "Pressure decay within site limit (TBD by engineer)", "Seal and fitting integrity"),
                (f"R-{cid}-AIRLOSS", "Air-loss behaviour",
                 "Remove supply during a cycle; observe and record the cylinder state.",
                 "Behaviour matches documented safe state (TBD by engineer)", "Behaviour on air loss is acceptable"),
                (f"R-{cid}-PWRLOSS", "Power-loss behaviour",
                 "De-energise during a cycle; record final position.",
                 "Final position matches documented fail state (TBD by engineer)",
                 "Assumed spring-return / detent behaviour on power loss"),
            ]
            for r, t, s, cr, a in tests:
                n += 1
                rows.append(_row(n, alt["name"], f"{cid} ({ln['part_number']})", r, t, s, cr, a))
        elif ln["role"] == "valve":
            n += 1
            rows.append(_row(n, alt["name"], f"{cid} ({ln['part_number']})", f"R-{cid}-SWITCH", "Valve switching test",
                             "Energise each coil from PLC; confirm motion direction and port function.",
                             "Each output moves the correct cylinder in the correct direction",
                             "Valve function and DO address mapping"))
        elif ln["role"] == "service_unit":
            n += 1
            rows.append(_row(n, alt["name"], f"{cid} ({ln['part_number']})", f"R-{cid}-PRESS", "Supply pressure check",
                             "Set regulator; measure at the furthest consumer during a full cycle.",
                             f"Pressure at consumers >= {c.requirements.supply_pressure_bar or 'TBD'} bar during cycle",
                             "Service unit flow is sufficient with all cylinders moving"))
        elif ln["role"] == "pressure_switch":
            n += 1
            rows.append(_row(n, alt["name"], f"{cid} ({ln['part_number']})", f"R-{cid}-SETPOINT", "Switch point check",
                             "Ramp pressure up and down with a reference gauge; note the PLC input change.",
                             "Switch points within tolerance of the setpoint (TBD by engineer)",
                             "Pressure switch range, output type and hysteresis are equivalent"))
        elif ln["role"] in ("vacuum_pad", "vacuum_ejector"):
            n += 1
            rows.append(_row(n, alt["name"], f"{cid} ({ln['part_number']})", f"R-{cid}-VACUUM", "Vacuum hold and release test",
                             "Pick the real workpiece; record vacuum level, time to reach it, and drop after supply is cut.",
                             "Holds the workpiece for the site-defined time after air loss (TBD by engineer)",
                             "Vacuum gripper behaviour on air and power loss is acceptable"))
    for r in io_rows:
        if str(r["new_channel"]).startswith("HOLD"):
            continue
        n += 1
        rows.append(_row(n, alt["name"], r["old_tag"], f"R-IO-{r['old_tag']}", "I/O point-to-point check",
                         f"Force/read {r['new_channel']} and confirm effect on {r['component'] or 'unknown'}.",
                         "Signal reaches the correct device and PLC address", "I/O migration mapping is correct"))
    return rows
