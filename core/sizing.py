"""Screening-level sizing. NOT a substitute for manufacturer sizing tools or engineer verification."""
import math

G = 9.81
SAFETY_FACTOR = 1.5      # shared by sizing and the electrification screen
EFFICIENCY = 0.85
ATM_BAR = 1.013


def required_bore_mm(load_kg, pressure_bar, safety_factor=SAFETY_FACTOR, efficiency=EFFICIENCY):
    force_n = load_kg * G * safety_factor
    area_m2 = force_n / (pressure_bar * 1e5 * efficiency)
    return 2000 * math.sqrt(area_m2 / math.pi)


def cylinder_force_n(bore_mm, rod_mm, pressure_bar, efficiency=0.85):
    a_cap = math.pi * (bore_mm / 2000) ** 2
    a_ann = a_cap - math.pi * (rod_mm / 2000) ** 2
    return pressure_bar * 1e5 * a_cap * efficiency, pressure_bar * 1e5 * a_ann * efficiency


def air_volume_l(bore_mm, stroke_mm):
    return math.pi * (bore_mm / 2000) ** 2 * (stroke_mm / 1000) * 1000


def required_flow_lpm(bore_mm, stroke_mm, stroke_time_s, pressure_bar):
    """Average free-air flow over one stroke (litres/min, normal conditions)."""
    free_air_l = air_volume_l(bore_mm, stroke_mm) * (pressure_bar + ATM_BAR) / ATM_BAR
    return free_air_l / stroke_time_s * 60


def assess_cylinder(comp, supply_bar, safety_factor=SAFETY_FACTOR, efficiency=EFFICIENCY):
    """Return a transparent record of inputs, results and what still needs verification."""
    p = comp.pressure_bar or supply_bar
    rec = {
        "component_id": comp.id,
        "inputs": {"load_kg": comp.load_kg, "pressure_bar": p, "safety_factor": safety_factor,
                   "efficiency": efficiency, "existing_bore_mm": comp.bore_mm,
                   "stroke_mm": comp.stroke_mm, "stroke_time_s": comp.stroke_time_s},
        "required_bore_mm": None, "required_flow_lpm": None, "existing_bore_ok": None,
        "status": "unresolved", "notes": [],
        "assumptions": ["Load treated as a vertical lift (F = m*g*SF); friction, acceleration and load "
                        "direction not modelled",
                        f"Mechanical efficiency {efficiency}", f"Safety factor {safety_factor}"],
        "verify": ["Load direction and extension/retraction force", "Mounting and buckling for long strokes",
                   "Actual pressure at the actuator", "Speed / cushioning / duty cycle"],
    }
    if not p:
        rec["notes"].append("No pressure known: cannot size.")
        return rec
    if comp.load_kg:
        rec["required_bore_mm"] = round(required_bore_mm(comp.load_kg, p, safety_factor, efficiency), 1)
        rec["status"] = "calculated"
        if comp.bore_mm:
            rec["existing_bore_ok"] = comp.bore_mm >= rec["required_bore_mm"]
            if not rec["existing_bore_ok"]:
                rec["notes"].append("Existing bore is below the theoretical requirement for the stated load. "
                                    "Either the load figure is wrong or the machine was under-sized: ask the site engineer.")
    else:
        rec["notes"].append("No load stated: bore cannot be checked.")
    bore = comp.bore_mm or rec["required_bore_mm"]
    if bore and comp.stroke_mm and comp.stroke_time_s:
        rec["required_flow_lpm"] = round(required_flow_lpm(bore, comp.stroke_mm, comp.stroke_time_s, p), 1)
    elif comp.stroke_mm:
        rec["notes"].append("No stroke time: valve flow cannot be sized.")
    return rec
