"""Run:  python -m tests.test_core      (or pytest). No network, no API key needed."""
import io
import json
import tempfile
from pathlib import Path

import openpyxl

import core.xref as xref
from core.export import build_workbook, build_xref_workbook
from core.extract import parse_callouts, reconcile_with_text_layer
from core.pipeline import analyze
from core.schema import Circuit, Component

CASES = Path(__file__).resolve().parent.parent / "data" / "cases"


def load(name, src="truth"):
    return Circuit.model_validate_json((CASES / name / f"{src}.json").read_text())


def test_series_rules():
    assert xref.series_rule("KQ2T16-00A")[0] == "QST-16"
    assert xref.series_rule("KQ2T12-16A")[0] == "QST-16-12"          # larger tube first
    assert xref.series_rule("KQ2H16-03AS")[0] == "QS-3/8-16"
    assert xref.series_rule("KQ2R10-16A")[0] == "QS-16H-10"
    assert xref.series_rule("AN30-03")[0] == "UC-3/8"
    assert xref.series_rule("KQ2H06-M6A") is None                     # M6: abstain
    assert xref.series_rule("KQ2H06-U01N") is None                    # U-thread (Rc vs G) is ambiguous: abstain
    assert xref.series_rule("SY9320-5LOU-C10F-Q") is None


def test_normalise_and_tiers():
    assert xref.norm_code("kq2t12-00a 12 mm OD equal T") == "KQ2T12-00A"
    assert xref.norm_code("KQ2E08-00A ( BULKHEAD UNION )") == "KQ2E08-00A"
    assert xref.classify_tier("", "") == "T0"
    assert xref.classify_tier("QST-6", "") == "T1"
    assert xref.classify_tier("MS6-EM1-3/8", "Based on equivalent for X; check compatibility") == "T2"
    assert xref.classify_tier("QS-3/8-16", "Type code by series pattern; part number to be confirmed.") == "T3"
    assert "VCR" not in xref.clean_remark("Project 316 - VCR-tech s.r.o.; drawing item 4; No Festo equivalent identified")


def test_lookup_and_curated_override():
    idx = xref.get_index()
    r = idx.lookup("KQ2T16-00A", "SMC")
    assert r["candidates"][0]["festo_type_code"] == "QST-16"
    r = idx.lookup("Y300T-D", "SMC")                                   # curated "no equivalent" beats an old suggestion
    assert r["method"] == "none" and r["similar"]
    assert idx.lookup("ZZZ-NOT-A-CODE")["method"] == "none"


def test_every_part_gets_a_line_and_quantities_add_up():
    res = analyze(load("case3_smc_vacuum_manifold"))
    lfl = res["alternatives"][0]
    assert len(lfl["bom"]) == 44, "no component may be dropped"
    assert sum(g["qty"] for g in lfl["bom_grouped"]) == 44
    cov = res["coverage"]
    assert cov["lines"] == 44 and cov["no_match"] == len(res["gaps"][0]["legacy_ids"]) + sum(
        len(g["legacy_ids"]) for g in res["gaps"][1:])
    codes = {(f["code"], f["component_id"]) for f in res["flags"]}
    assert ("CLOSED_CENTRE_TRAPPED_PRESSURE", "P33") in codes and ("VACUUM_LOSS_LOAD_DROP", "P20") in codes


def test_old_cases_still_work():
    res = analyze(load("case1_press_fixture"))
    assert any(f["code"] == "SAFETY_ESCALATE" for f in res["flags"])
    assert res["coverage"]["escalated"] == 1


def test_text_layer_reconcile():
    co = parse_callouts("10: DN 7,2 rychlospojka\n1: KQ2T16-00A\n33: SY9320-5LOU-C10F-Q\n16mm\n14, 15\n")
    assert co == {1: "KQ2T16-00A", 33: "SY9320-5LOU-C10F-Q"}
    c = Circuit(components=[Component(id="P1", type="fitting", callout_no=1, part_number="KQ2T16-OOA")])
    out = reconcile_with_text_layer(c, co)
    assert out.components[0].part_number == "KQ2T16-00A"               # mis-copied letter O fixed
    assert any(x.callout_no == 33 and x.confidence <= 0.5 for x in out.components)


def test_feedback_changes_ranking():
    old = xref.FEEDBACK
    with tempfile.TemporaryDirectory() as d:
        xref.FEEDBACK = Path(d) / "fb.jsonl"
        try:
            top = xref.get_index().lookup("AN20-02", "SMC")["candidates"][0]["festo_type_code"]
            xref.record_feedback("SMC", "AN20-02", top, "reject")
            after = xref.get_index().lookup("AN20-02", "SMC")["candidates"]
            assert after[0]["festo_type_code"] != top and after[-1]["rejected"]
        finally:
            xref.FEEDBACK = old
            xref.reload_index()


def test_exports_open():
    res = analyze(load("case3_smc_vacuum_manifold"))
    wb = openpyxl.load_workbook(io.BytesIO(build_workbook(res, "instrumented")))
    assert "Xref (request format)" in wb.sheetnames and "Gaps" in wb.sheetnames
    ws = openpyxl.load_workbook(io.BytesIO(build_xref_workbook(res))).active
    assert [c.value for c in ws[1]][11] == "Festo type code" and ws.max_row == 45


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok ", name)
