"""Generates the synthetic demo cases. Run once: python tools/make_cases.py
 truth.json          hand-defined ground truth (what a perfect extraction would return)
 extracted.json      SIMULATED extraction with deliberate errors, so the correction screen has work to do.
                     Replace with real cached Gemini output for your own diagrams.
 expert_baseline.json PLACEHOLDER BOM picks. Must be authored/reviewed by a real domain expert."""
import json, csv, copy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "data" / "cases"


def comp(id, type, label, conf=1.0, **kw):
    return {"id": id, "type": type, "label": label, "confidence": conf, **kw}


def con(a, ap, b, bp, kind="pneumatic", conf=1.0):
    return {"from_component": a, "from_port": ap, "to_component": b, "to_port": bp,
            "connection_type": kind, "confidence": conf}


def write(case, truth, extracted, io_rows, baseline, expected_flags, truth_safety, manual_minutes):
    d = ROOT / case
    d.mkdir(parents=True, exist_ok=True)
    (d / "truth.json").write_text(json.dumps(truth, indent=2))
    (d / "extracted.json").write_text(json.dumps(extracted, indent=2))
    with open(d / "io_list.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tag", "direction", "linked_component", "description", "plc_address"])
        w.writerows(io_rows)
    (d / "expert_baseline.json").write_text(json.dumps({
        "WARNING": "PLACEHOLDER written by the tool author. Replace with picks made independently by a domain expert.",
        "like_for_like": baseline, "expected_flags": expected_flags, "truth_safety": truth_safety,
        "manual_minutes": manual_minutes}, indent=2))


def io_points(rows):
    return [{"tag": r[0], "direction": r[1], "linked_component": r[2], "description": r[3], "plc_address": r[4]}
            for r in rows]


# ---------------- case1: automotive press fixture ----------------
io1 = [("Y1", "DO", "V1", "Clamp valve coil", "%Q0.0"), ("Y2A", "DO", "V2", "Press extend coil", "%Q0.1"),
       ("Y2B", "DO", "V2", "Press retract coil", "%Q0.2"), ("Y3", "DO", "V3", "E-stop dump valve coil", "%Q0.3"),
       ("X1", "DI", "S1", "Clamp retracted", "%I0.0"), ("X2", "DI", "S2", "Clamp extended", "%I0.1"),
       ("X3", "DI", "S3", "Press retracted", "%I0.2")]
t1 = {
    "components": [
        comp("SU1", "service_unit", "FRL unit", pressure_bar=6),
        comp("V3", "valve", "E-stop dump valve", valve_function="3/2 single solenoid spring return",
             voltage="24VDC", safety_status="engineer_confirmed_safety_related"),
        comp("V1", "valve", "Clamp valve", part_number="LEGACY-V52S-A",
             valve_function="5/2 single solenoid spring return", voltage="24VDC"),
        comp("V2", "valve", "Press valve", part_number="LEGACY-V52D-A",
             valve_function="5/2 double solenoid", voltage="24VDC"),
        comp("C1", "cylinder", "Clamp cylinder", bore_mm=40, stroke_mm=100, load_kg=20, stroke_time_s=0.5),
        comp("C2", "cylinder", "Press cylinder", bore_mm=63, stroke_mm=200, load_kg=100, stroke_time_s=1.0),
        comp("S1", "sensor", "Clamp retracted"), comp("S2", "sensor", "Clamp extended"),
        comp("S3", "sensor", "Press retracted")],
    "connections": [con("SU1", "OUT", "V3", "1"), con("V3", "2", "V1", "1"), con("V3", "2", "V2", "1"),
                    con("V1", "2", "C1", "A"), con("V1", "4", "C1", "B"),
                    con("V2", "2", "C2", "A"), con("V2", "4", "C2", "B"),
                    con("S1", None, "C1", None, "signal"), con("S2", None, "C1", None, "signal"),
                    con("S3", None, "C2", None, "signal")],
    "io": io_points(io1),
    "requirements": {"supply_pressure_bar": 6, "cycles_per_hour": 400,
                     "existing_controller": "legacy relay/PLC", "target_controller": "Ethernet-based PLC"},
    "unknowns": [], "assumptions": ["Loads are as stated by the plant; vertical-lift worst case assumed."]}
e1 = copy.deepcopy(t1)
for c in e1["components"]:
    if c["id"] == "C2": c["bore_mm"] = 50; c["confidence"] = 0.55          # misread bore
    if c["id"] == "V1": c["valve_function"] = None; c["confidence"] = 0.6   # unreadable symbol
    if c["id"] == "V3": c["safety_status"] = "unknown"; c["confidence"] = 0.8  # model missed safety role
e1["connections"] = [k for k in e1["connections"] if not (k["from_component"] == "V2" and k["from_port"] == "4")]
base1 = {"C1": "DEMO-CYL-40", "C2": "DEMO-CYL-63", "V1": "DEMO-VLV-52S-700", "V2": "DEMO-VLV-52D-700",
         "SU1": "DEMO-FRL-14", "S1": "DEMO-SNS-PNP", "S2": "DEMO-SNS-PNP", "S3": "DEMO-SNS-PNP", "V3": "ESCALATE"}
write("case1_press_fixture", t1, e1, io1, base1,
      [["SAFETY_ESCALATE", "V3"], ["ONE_END_SENSOR", "C2"], ["FAILSAFE_ASSUMPTION", "V1"],
       ["FAILSAFE_ASSUMPTION", "V2"]], ["V3"], 90)

# ---------------- case2: packaging machine, missing data ----------------
io2 = [("Y1", "DO", "V1", "Infeed pusher", "%Q0.0"), ("Y2", "DO", "V2", "Lid press", "%Q0.1"),
       ("X1", "DI", "S1", "Pusher home", "%I0.0")]
t2 = {
    "components": [
        comp("SU1", "service_unit", "FRL unit"),
        comp("V1", "valve", "Pusher valve", valve_function="5/2 single solenoid spring return"),
        comp("V2", "valve", "Lid press valve", valve_function="5/2 single solenoid spring return"),
        comp("V3", "valve", "Sealer valve"),
        comp("C1", "cylinder", "Infeed pusher", bore_mm=32, stroke_mm=150, load_kg=5, stroke_time_s=0.6),
        comp("C2", "cylinder", "Lid press", bore_mm=32, stroke_mm=80, load_kg=3),
        comp("C3", "cylinder", "Sealer", bore_mm=50, stroke_mm=250),
        comp("S1", "sensor", "Pusher home")],
    "connections": [con("SU1", "OUT", "V1", "1"), con("SU1", "OUT", "V2", "1"), con("SU1", "OUT", "V3", "1"),
                    con("V1", "2", "C1", "A"), con("V1", "4", "C1", "B"),
                    con("V2", "2", "C2", "A"), con("V2", "4", "C2", "B"),
                    con("V3", "2", "C3", "A"), con("V3", "4", "C3", "B"),
                    con("S1", None, "C1", None, "signal")],
    "io": io_points(io2), "requirements": {"target_controller": "Ethernet-based PLC"},
    "unknowns": ["Supply pressure not on drawing", "V3 coil/valve function unreadable"], "assumptions": []}
e2 = copy.deepcopy(t2)
e2["components"] = [c for c in e2["components"] if c["id"] != "S1"]
e2["connections"] = [k for k in e2["connections"] if k["from_component"] != "S1"]
for c in e2["components"]:
    if c["id"] == "C3": c["confidence"] = 0.6
base2 = {"C1": "DEMO-CYL-32", "C2": "DEMO-CYL-32", "C3": "DEMO-CYL-50", "V1": "DEMO-VLV-52S-400",
         "V2": "DEMO-VLV-52S-400", "V3": None, "SU1": "DEMO-FRL-14", "S1": "DEMO-SNS-PNP"}
write("case2_packer_missing_data", t2, e2, io2, base2,
      [["NO_PRESSURE", "CIRCUIT"], ["VALVE_FUNCTION_UNKNOWN", "V3"], ["NO_END_SENSORS", "C2"],
       ["NO_END_SENSORS", "C3"], ["NO_DO", "V3"], ["ONE_END_SENSOR", "C1"]], [], 75)

# ---------------- case3: real SMC supply + vacuum + manifold circuit (project 316, anonymised) ----------------
# Parts and type codes come from the refined parts sheet. Topology was read by hand from the PDF drawing
# (item numbers = callouts on the drawing). Cylinders are not drawn: the valves feed two unlabelled units.
SMC = "SMC"
ITEMS = [  # callout, type, code, label, extra
    (1, "fitting", "KQ2T16-00A", "Push-in tee 16 mm", {}),
    (2, "shutoff_valve", "VHS30-F03-M-D", "Shut-off / relief valve G3/8", {}),
    (3, "regulator", "AW30-F03H-D", "Filter regulator G3/8", {}),
    (4, "accessory", "Y300T-D", "Bracket for AW30", {}),
    (5, "gauge", "G36-10-01-L", "Pressure gauge", {}),
    (6, "silencer", "AN20-02", "Silencer R1/4 (shut-off exhaust)", {}),
    (7, "fitting", "KQ2H16-03AS", "Male connector 16 mm x R3/8", {}),
    (8, "pressure_switch", "ISE20-P-M5-L", "Pressure switch M5", {}),
    (9, "fitting", "KQ2H16-03AS", "Male connector 16 mm x R3/8", {}),
    (10, "other", None, "Quick coupling DN 7.2 (no code on drawing)", {"manufacturer": None}),
    (11, "fitting", "KQ2T16-00A", "Push-in tee 16 mm", {}),
    (12, "fitting", "KQ2R06-10A", "Reducer 6 / 10 mm", {}),
    (13, "fitting", "KQ2R10-16A", "Reducer 10 / 16 mm", {}),
    (14, "valve", "VT307-5DZ1-01F-Q", "3/2 solenoid valve (vacuum supply)", {"valve_function": "3/2 single solenoid spring return"}),
    (15, "accessory", "DXT152-25-1A", "Accessory for VT307", {}),
    (16, "fitting", "KQ2H06-01AS", "Male connector 6 mm x R1/8", {}),
    (17, "fitting", "KQ2H06-01AS", "Male connector 6 mm x R1/8", {}),
    (18, "silencer", "AN10-01", "Silencer R1/8 (valve exhaust)", {}),
    (19, "fitting", "KQ2T06-00A", "Push-in tee 6 mm", {}),
    (20, "vacuum_pad", "ZPT40BGN-B6", "Vacuum pad, bellows, dia 40", {}),
    (21, "fitting", "KQ2H06-M6A", "Male connector 6 mm x M6", {}),
    (22, "fitting", "KQ2T06-00A", "Push-in tee 6 mm", {}),
    (23, "vacuum_pad", "ZPT40BGN-B6", "Vacuum pad, bellows, dia 40", {}),
    (24, "fitting", "KQ2H06-M6A", "Male connector 6 mm x M6", {}),
    (25, "manifold", "SS5Y9-23-04-00F-Q", "Manifold base SY9000, 4 stations", {}),
    (26, "silencer", "AN30-03", "Silencer R3/8", {}), (27, "silencer", "AN30-03", "Silencer R3/8", {}),
    (28, "silencer", "AN30-03", "Silencer R3/8", {}), (29, "silencer", "AN30-03", "Silencer R3/8", {}),
    (30, "fitting", "KQ2H12-03AS", "Male connector 12 mm x R3/8", {}),
    (31, "fitting", "KQ2H12-03AS", "Male connector 12 mm x R3/8", {}),
    (32, "fitting", "KQ2T12-16A", "Push-in tee 12 / 16 mm", {}),
] + [(n, "valve", "SY9320-5LOU-C10F-Q", f"5/3 closed-centre valve {i+1}",
      {"valve_function": "5/3 closed centre double solenoid"}) for i, n in enumerate((33, 35, 37, 39))] + [
    (n, "accessory", "SY100-68-A-30", f"Lead wire for valve {i+1}", {}) for i, n in enumerate((34, 36, 38, 40))] + [
    (41, "vacuum_ejector", "ZH10DSA-06-06-08", "Vacuum ejector with silencer", {}),
    (42, "silencer", "AN15-C08", "Push-in silencer 8 mm", {}),
    (43, "other", "PMHPD3T200-20Z", "Unnumbered unit 1 of 2 (maker not confirmed)", {"confidence": 0.5}),
    (44, "other", "PMHPD3T200-20Z", "Unnumbered unit 2 of 2 (maker not confirmed)", {"confidence": 0.5})]
ITEMS.sort(key=lambda x: x[0])


def item_comp(n, typ, code, label, extra):
    kw = {"manufacturer": SMC, **extra}
    kw = {k: v for k, v in kw.items() if v is not None}
    return comp(f"P{n}", typ, label, part_number=code, callout_no=n, **kw)


t3 = {"components": [item_comp(*x) for x in ITEMS], "connections": [], "io": [],
      "requirements": {"existing_controller": "unknown", "target_controller": "Ethernet-based PLC"},
      "unknowns": ["Supply pressure not on the drawing", "Vacuum switch symbol next to item 19 has no item number",
                   "Manufacturer of the two PMHPD3T200-20Z units not confirmed"],
      "assumptions": ["Part codes come from the drawing's text layer; topology was read by hand.",
                      "The I/O list is an illustrative example, it is not on the drawing."]}
chain = [("P7", "P2"), ("P2", "P3"), ("P3", "P5"), ("P3", "P8"), ("P3", "P9"), ("P9", "P1"), ("P1", "P10"),
         ("P1", "P11"), ("P11", "P32"), ("P11", "P13"), ("P13", "P12"), ("P12", "P17"), ("P16", "P41"),
         ("P41", "P42"), ("P41", "P19"), ("P19", "P22"), ("P22", "P21"), ("P22", "P24"), ("P21", "P20"),
         ("P24", "P23"), ("P32", "P30"), ("P32", "P31"), ("P30", "P25"), ("P31", "P25"),
         ("P25", "P26"), ("P25", "P27"), ("P25", "P28"), ("P25", "P29")]
conns = [con(a, None, b, None) for a, b in chain]
conns += [con("P2", "3", "P6", None), con("P17", None, "P14", "1"), con("P14", "2", "P16", None),
          con("P14", "3", "P18", None)]
for v in ("P33", "P35", "P37", "P39"):
    conns.append(con("P25", None, v, "1"))
for v, load in (("P33", "P43"), ("P35", "P43"), ("P37", "P44"), ("P39", "P44")):
    conns += [con(v, "2", load, None, conf=0.8), con(v, "4", load, None, conf=0.8)]
for lead, v in (("P34", "P33"), ("P36", "P35"), ("P38", "P37"), ("P40", "P39"), ("P15", "P14"), ("P4", "P3")):
    conns.append(con(lead, None, v, None, "signal"))
t3["connections"] = conns
io3 = [("Y33A", "DO", "P33", "Valve 1 coil A (example)", "%Q0.0"), ("Y33B", "DO", "P33", "Valve 1 coil B (example)", "%Q0.1"),
       ("Y35A", "DO", "P35", "Valve 2 coil A (example)", "%Q0.2"), ("Y35B", "DO", "P35", "Valve 2 coil B (example)", "%Q0.3"),
       ("Y37A", "DO", "P37", "Valve 3 coil A (example)", "%Q0.4"), ("Y37B", "DO", "P37", "Valve 3 coil B (example)", "%Q0.5"),
       ("Y39A", "DO", "P39", "Valve 4 coil A (example)", "%Q0.6"), ("Y39B", "DO", "P39", "Valve 4 coil B (example)", "%Q0.7"),
       ("Y14", "DO", "P14", "Vacuum valve coil (example)", "%Q1.0"), ("X8", "DI", "P8", "Supply pressure switch (example)", "%I0.0")]
t3["io"] = io_points(io3)
e3 = copy.deepcopy(t3)
e3["connections"] = [k for k in e3["connections"] if not (k["from_component"] == "P22" and k["to_component"] == "P24")
                     and not (k["from_component"] == "P24" and k["to_component"] == "P23")]   # missed one pad branch
for c in e3["components"]:
    if c["id"] == "P14": c["valve_function"] = None; c["confidence"] = 0.6                     # symbol unreadable
    if c["id"] == "P37": c["valve_function"] = "5/2 double solenoid"; c["label"] = "Solenoid valve 3"; c["confidence"] = 0.6    # centre position misread
    if c["id"] == "P8": c["confidence"] = 0.6
REFINED = {1: "QST-16", 2: "MS6-EM1-3/8", 3: "MS6-LFR-3/8-D7-C-R-M-AS", 4: None, 5: "MA-40-1,0-R1/8-MPA-E-RG", 6: "UC-1/4",
           7: "QS-3/8-16", 8: "SPAN-B11R-M5F-PN-PN-L1", 9: "QS-3/8-16", 10: None, 11: "QST-16", 12: "QS-10H-6",
           13: "QS-16H-10", 14: "MHE3-M1H-3/2G-1/8-K", 15: None, 16: "QS-1/8-6", 17: "QS-1/8-6", 18: "UC-1/8",
           19: "QST-6", 20: "ESS-40-BN", 21: None, 22: "QST-6", 23: "ESS-40-BN", 24: None, 25: "VABM-B10-30E-G34-4",
           26: "UC-3/8", 27: "UC-3/8", 28: "UC-3/8", 29: "UC-3/8", 30: "QS-3/8-12", 31: "QS-3/8-12", 32: "QST-16-12",
           33: "VUVS-L30-P53C-MD-G38-F8-1C1", 34: "NEBV-H1G2-KN-2.5-N-LE2", 35: "VUVS-L30-P53C-MD-G38-F8-1C1",
           36: "NEBV-H1G2-KN-2.5-N-LE2", 37: "VUVS-L30-P53C-MD-G38-F8-1C1", 38: "NEBV-H1G2-KN-2.5-N-LE2",
           39: "VUVS-L30-P53C-MD-G38-F8-1C1", 40: "NEBV-H1G2-KN-2.5-N-LE2", 41: "VN-10-H-T3-PQ2-VQ2-RO1",
           42: "UC-QS-8H", 43: None, 44: None}
base3 = {f"P{n}": v for n, v in REFINED.items()}
write("case3_smc_vacuum_manifold", t3, e3, io3, base3,
      [["NO_PRESSURE", "CIRCUIT"], ["CLOSED_CENTRE_TRAPPED_PRESSURE", "P33"], ["CLOSED_CENTRE_TRAPPED_PRESSURE", "P35"],
       ["CLOSED_CENTRE_TRAPPED_PRESSURE", "P37"], ["CLOSED_CENTRE_TRAPPED_PRESSURE", "P39"],
       ["VACUUM_LOSS_LOAD_DROP", "P20"], ["VACUUM_LOSS_LOAD_DROP", "P23"]], [], 120)
bf = json.loads((ROOT / "case3_smc_vacuum_manifold" / "expert_baseline.json").read_text())
bf["WARNING"] = ("The expected Festo codes are the refined project sheet, which is ALSO loaded into the cross-reference "
                 "knowledge base. So this case is a smoke test of the pipeline, not accuracy evidence. manual_minutes "
                 "is a PLACEHOLDER.")
(ROOT / "case3_smc_vacuum_manifold" / "expert_baseline.json").write_text(json.dumps(bf, indent=2))

print("cases written")
