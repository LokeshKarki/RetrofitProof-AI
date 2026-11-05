"""Map existing I/O tags onto remote I/O channels for the instrumented alternative."""
from .schema import Circuit
from .graph import sensors_of, is_safety


def io_migration(c: Circuit, alt: dict) -> list[dict]:
    rows = []
    unsafe = {x.id for x in c.components if is_safety(x)}
    if alt["name"] != "instrumented":
        for p in c.io:
            rows.append({"old_tag": p.tag, "direction": p.direction, "component": p.linked_component,
                         "old_address": p.plc_address, "new_channel": p.plc_address or "unchanged",
                         "note": "Retained (no I/O change in this alternative)"})
        return rows
    counters = {"DI": 0, "DO": 0}
    def nxt(d):
        i = counters[d]; counters[d] += 1
        return f"RIO-{d}[{i // 16}].{i % 16}"
    for p in c.io:
        if p.linked_component in unsafe:
            rows.append({"old_tag": p.tag, "direction": p.direction, "component": p.linked_component,
                         "old_address": p.plc_address, "new_channel": "HOLD (safety review)",
                         "note": "Safety-related: not remapped until an engineer approves"})
        elif p.direction in counters:
            rows.append({"old_tag": p.tag, "direction": p.direction, "component": p.linked_component,
                         "old_address": p.plc_address, "new_channel": nxt(p.direction),
                         "note": "Remapped to remote I/O"})
    for comp in c.components:
        if comp.type == "cylinder" and not is_safety(comp):
            for k in range(max(0, 2 - len(sensors_of(c, comp.id)))):
                rows.append({"old_tag": f"NEW-{comp.id}-S{k+1}", "direction": "DI", "component": comp.id,
                             "old_address": None, "new_channel": nxt("DI"),
                             "note": "New end-position sensor: assign PLC tag and confirm which end"})
    return rows
