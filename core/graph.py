"""Port-aware circuit graph + deterministic risk flags."""
import re
import networkx as nx
from .schema import Circuit

SAFETY_PATTERN = re.compile(
    r"e-?stop|emergency|dump|safety|dual[- ]?channel|two[- ]?channel|2[- ]?channel|safe torque|guard",
    re.IGNORECASE)


def screen_safety(c: Circuit) -> Circuit:
    """Deterministic backstop: never rely on the vision model alone for safety detection.
    Anything matching a safety keyword becomes potentially_safety_related unless an engineer
    has explicitly cleared or confirmed it."""
    c = c.model_copy(deep=True)
    for comp in c.components:
        if comp.safety_status in ("engineer_confirmed_safety_related",
                                  "engineer_confirmed_not_safety_related"):
            continue
        text = " ".join(filter(None, [comp.label, comp.valve_function, comp.id]))
        if SAFETY_PATTERN.search(text):
            comp.safety_status = "potentially_safety_related"
    return c


def is_safety(comp) -> bool:
    return comp.safety_status in ("potentially_safety_related", "engineer_confirmed_safety_related")


def build_graph(c: Circuit) -> nx.MultiDiGraph:
    G = nx.MultiDiGraph()
    for comp in c.components:
        G.add_node(comp.id, **comp.model_dump())
    for k in c.connections:
        if k.from_component in G and k.to_component in G:
            G.add_edge(k.from_component, k.to_component, from_port=k.from_port,
                       to_port=k.to_port, kind=k.connection_type, confidence=k.confidence)
    return G


def sensors_of(c: Circuit, cyl_id: str) -> list[str]:
    ids = {x.id: x for x in c.components}
    out = []
    for k in c.connections:
        if k.connection_type != "signal":
            continue
        for a, b in ((k.from_component, k.to_component), (k.to_component, k.from_component)):
            if b == cyl_id and a in ids and ids[a].type == "sensor" and a not in out:
                out.append(a)
    return out


def downstream_cylinders(c: Circuit, valve_id: str) -> list[str]:
    ids = {x.id: x for x in c.components}
    out = []
    for k in c.connections:
        if k.connection_type != "pneumatic":
            continue
        for a, b in ((k.from_component, k.to_component), (k.to_component, k.from_component)):
            if a == valve_id and b in ids and ids[b].type == "cylinder" and b not in out:
                out.append(b)
    return out


CLOSED_CENTRE = re.compile(r"closed[- ]?cent(?:re|er)|\b5/3\b.*\bclosed|p53c", re.IGNORECASE)
VACUUM_TYPES = ("vacuum_pad", "vacuum_ejector")


def is_closed_centre(comp) -> bool:
    return comp.type == "valve" and bool(CLOSED_CENTRE.search(" ".join(filter(None, [comp.valve_function, comp.label]))))


def _flag(cid, sev, code, msg):
    return {"component_id": cid, "severity": sev, "code": code, "message": msg}


def analyze_flags(c: Circuit) -> list[dict]:
    flags = []
    ids = {x.id: x for x in c.components}
    pneu = [k for k in c.connections if k.connection_type == "pneumatic"]

    for comp in c.components:
        if is_safety(comp):
            flags.append(_flag(comp.id, "critical", "SAFETY_ESCALATE",
                "Potential safety function. NOT substituted. Engineer must supply approved safety "
                "requirements; no validation tests are generated for this item."))
        if comp.confidence < 0.7:
            flags.append(_flag(comp.id, "warning", "LOW_CONFIDENCE",
                f"Extraction confidence {comp.confidence:.2f}. Verify against the drawing."))

    for comp in c.components:
        if is_closed_centre(comp):
            flags.append(_flag(comp.id, "warning", "CLOSED_CENTRE_TRAPPED_PRESSURE",
                "Closed-centre 5/3: when de-energised all working ports are blocked, so pressure stays trapped and a "
                "load can drift or be held. Confirm the intended state with the site engineer; do not swap for a "
                "different centre position."))
        if comp.type == "vacuum_pad":
            flags.append(_flag(comp.id, "warning", "VACUUM_LOSS_LOAD_DROP",
                "Vacuum gripper: holding force is lost when air or power is lost, so the load can drop. Check for a "
                "vacuum switch, check valve or mechanical safeguard. Not assessed as a safety function by this tool."))

    # pressure
    known_p = c.requirements.supply_pressure_bar or any(x.pressure_bar for x in c.components)
    if not known_p:
        flags.append(_flag("CIRCUIT", "warning", "NO_PRESSURE",
            "No supply/working pressure known. Sizing results are unresolved."))

    for comp in c.components:
        if comp.type == "cylinder":
            n = len(sensors_of(c, comp.id))
            if n == 0:
                flags.append(_flag(comp.id, "warning", "NO_END_SENSORS",
                    "No end-position sensors found. Instrumented alternative recommended."))
            elif n == 1:
                flags.append(_flag(comp.id, "info", "ONE_END_SENSOR",
                    "Only one end-position sensor found. Confirm whether the other end is unmonitored."))
            ports = set()
            for k in pneu:
                if k.to_component == comp.id and k.to_port:
                    ports.add(k.to_port)
                if k.from_component == comp.id and k.from_port:
                    ports.add(k.from_port)
            if not any(comp.id in (k.from_component, k.to_component) for k in pneu):
                flags.append(_flag(comp.id, "warning", "ORPHAN",
                    "No pneumatic connection found. Possible misread."))
            elif len(ports) < 2:
                flags.append(_flag(comp.id, "warning", "ONE_CYL_PORT",
                    "Only one cylinder port connected. Possible missed connection or single-acting cylinder."))
            if comp.load_kg is None:
                flags.append(_flag(comp.id, "warning", "NO_LOAD",
                    "Load not stated. Cylinder sizing cannot be checked."))
            if comp.stroke_time_s is None:
                flags.append(_flag(comp.id, "info", "NO_STROKE_TIME",
                    "Required stroke time not stated. Valve flow sizing cannot be checked."))
        elif comp.type == "valve" and not is_safety(comp):
            f = (comp.valve_function or "").lower()
            if not f:
                flags.append(_flag(comp.id, "warning", "VALVE_FUNCTION_UNKNOWN",
                    "Valve function / fail-safe state unspecified. Substitution unresolved."))
            elif "double" in f:
                flags.append(_flag(comp.id, "info", "FAILSAFE_ASSUMPTION",
                    "Double solenoid: assumed to hold last position on power loss. Confirm with site engineer."))
            elif "single" in f:
                flags.append(_flag(comp.id, "info", "FAILSAFE_ASSUMPTION",
                    "Single solenoid: assumed to return to spring position on power loss. Confirm with site engineer."))
            if not any(comp.id in (k.from_component, k.to_component) for k in pneu):
                flags.append(_flag(comp.id, "warning", "ORPHAN",
                    "No pneumatic connection found. Possible misread."))

    # I/O consistency
    io_by_comp = {}
    for p in c.io:
        if p.linked_component:
            if p.linked_component not in ids:
                flags.append(_flag(p.tag, "warning", "IO_UNKNOWN_COMPONENT",
                    f"I/O tag links to '{p.linked_component}', which is not in the circuit."))
            io_by_comp.setdefault(p.linked_component, []).append(p)
        else:
            flags.append(_flag(p.tag, "warning", "IO_UNLINKED", "I/O point has no linked component."))
    for comp in c.components:
        if comp.type == "valve":
            n_do = sum(1 for p in io_by_comp.get(comp.id, []) if p.direction == "DO")
            need = 2 if "double" in (comp.valve_function or "").lower() else 1
            if n_do == 0:
                flags.append(_flag(comp.id, "warning", "NO_DO",
                    "No digital output linked to this valve in the I/O list."))
            elif n_do < need:
                flags.append(_flag(comp.id, "warning", "MISSING_COIL",
                    f"Double solenoid valve has {n_do} linked output(s); expected 2."))
        if comp.type in ("sensor", "pressure_switch") and not any(p.direction == "DI" for p in io_by_comp.get(comp.id, [])):
            flags.append(_flag(comp.id, "warning", "NO_DI", "Sensor has no digital input in the I/O list."))

    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(flags, key=lambda f: order[f["severity"]])


def to_dot(c: Circuit, flags: list[dict]) -> str:
    flagged = {f["component_id"]: f["severity"] for f in reversed(flags)}
    shape = {"cylinder": "box", "valve": "diamond", "service_unit": "hexagon",
             "sensor": "ellipse", "fitting": "circle", "other": "box"}
    shape = {**{t: "box" for t in ("pressure_switch", "gauge", "regulator", "shutoff_valve", "shuttle_valve",
                                   "silencer", "manifold", "vacuum_ejector", "vacuum_pad", "accessory")}, **shape}
    lines = ["digraph G { rankdir=LR; node [fontsize=11];"]
    for comp in c.components:
        colour = "white"
        sev = flagged.get(comp.id)
        if sev == "critical" or is_safety(comp):
            colour = "#f4a3a3"
        elif sev == "warning":
            colour = "#ffe39a"
        lab = f"{comp.id}\\n{comp.label or comp.type}"
        lines.append(f'"{comp.id}" [label="{lab}", shape={shape.get(comp.type, "box")}, style=filled, fillcolor="{colour}"];')
    for k in c.connections:
        style = "dashed" if k.connection_type == "signal" else "solid"
        lab = f"{k.from_port or '?'}→{k.to_port or '?'}"
        lines.append(f'"{k.from_component}" -> "{k.to_component}" [label="{lab}", style={style}];')
    lines.append("}")
    return "\n".join(lines)
